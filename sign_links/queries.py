"""SQL-слой раздела «Ссылка на подписание».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют ни
пулом, ни транзакцией — их держит вызывающий. Так же устроены parcels,
driver_chats, wiki и crm.
"""

from datetime import datetime, timedelta

from . import access
from .schema import VENDOR_OUTCOMES

# Смещение Алматы от UTC. Render живёт в UTC, а «сегодня» для дневного предела
# должно кончаться в полночь по Алматы, а не в пять утра. Сдвигом, а не
# ZoneInfo: у Казахстана с 01.03.2024 одна зона без перевода часов, а tzdata
# на контейнере может и отсутствовать (тот же приём, что у parcels.queries).
_ALMATY_OFFSET = timedelta(hours=5)


def now_almaty():
    return datetime.utcnow() + _ALMATY_OFFSET


def today_start_almaty():
    return now_almaty().replace(hour=0, minute=0, second=0, microsecond=0)


# ─────────────────────────────────────────────────────────────────────────────
# Контекст доступа
# ─────────────────────────────────────────────────────────────────────────────

_ACCESS_CONTEXT_SQL = """
WITH me AS (
    SELECT id, name, role, department_id
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
    COALESCE((SELECT array_agg(id)   FROM headed), '{}'),
    COALESCE((SELECT array_agg(code) FROM headed), '{}')
"""


def load_access_context(cursor, user_id):
    """Профиль + периметр одним запросом."""
    cursor.execute(_ACCESS_CONTEXT_SQL, {'user_id': int(user_id)})
    row = cursor.fetchone()
    if not row or row[1] is None:
        return None
    name, role, department_id, department_code, headed, headed_codes = row
    return {
        'user_id': int(user_id),
        'name': name,
        'role': access.normalize_role(role),
        'department_id': department_id,
        'department_code': department_code,
        'headed_department_ids': list(headed or []),
        'headed_department_codes': list(headed_codes or []),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Запись в журнал
# ─────────────────────────────────────────────────────────────────────────────

_INSERT_SQL = """
    INSERT INTO sign_link_requests
        (user_id, user_name, user_role, department_id, department_code,
         iin, outcome, vendor_message, link_issued, link_host, error_text,
         latency_ms, ip_address, user_agent)
    VALUES
        (%(user_id)s, %(user_name)s, %(user_role)s, %(department_id)s,
         %(department_code)s, %(iin)s, %(outcome)s, %(vendor_message)s,
         %(link_issued)s, %(link_host)s, %(error_text)s, %(latency_ms)s,
         %(ip)s, %(ua)s)
    RETURNING id, created_at
"""


def log_request(cursor, ctx, *, iin, outcome, vendor_message=None, link_issued=False,
                link_host=None, error_text=None, latency_ms=None, ip_address=None,
                user_agent=None):
    """Строка журнала. Снимок человека берётся из контекста, а не джойнится потом.

    Отдел — тот, в котором человек состоит; у главы отдела без членства
    (глава числится в своём же отделе, но случай «глава без отдела» бывает)
    берём первый из руководимых отделов раздела: журнал главы фильтруется
    именно по этому коду, и без него свои же запросы глава не увидел бы.
    """
    department_code = str(ctx.get('department_code') or '').strip().lower() or None
    if not department_code:
        headed = access.headed_section_codes(ctx)
        department_code = headed[0] if headed else None
    cursor.execute(_INSERT_SQL, {
        'user_id': int(ctx['user_id']),
        'user_name': ctx.get('name'),
        'user_role': ctx.get('role'),
        'department_id': ctx.get('department_id'),
        'department_code': department_code,
        'iin': str(iin or '')[:32],
        'outcome': outcome,
        'vendor_message': (vendor_message or '')[:1000] or None,
        'link_issued': bool(link_issued),
        'link_host': (link_host or '')[:120] or None,
        'error_text': (error_text or '')[:1000] or None,
        'latency_ms': int(latency_ms) if latency_ms is not None else None,
        'ip': (ip_address or '')[:64] or None,
        'ua': (user_agent or '')[:500] or None,
    })
    row = cursor.fetchone()
    return {'id': row[0], 'created_at': row[1].isoformat() if row and row[1] else None}


# ─────────────────────────────────────────────────────────────────────────────
# Дневной предел
# ─────────────────────────────────────────────────────────────────────────────

_USED_TODAY_SQL = """
    SELECT count(*)
      FROM sign_link_requests r
     WHERE r.user_id = %(user_id)s
       AND r.created_at >= %(day_start)s
       AND r.outcome = ANY(%(vendor_outcomes)s)
"""


def used_today(cursor, user_id):
    """Сколько раз человек сегодня СПРАШИВАЛ генератор (опечатки не в счёт)."""
    cursor.execute(_USED_TODAY_SQL, {
        'user_id': int(user_id),
        'day_start': today_start_almaty(),
        'vendor_outcomes': list(VENDOR_OUTCOMES),
    })
    row = cursor.fetchone()
    return int(row[0] or 0) if row else 0


# ─────────────────────────────────────────────────────────────────────────────
# Журнал
# ─────────────────────────────────────────────────────────────────────────────

# `scope` — граница главы отдела: NULL у глобального админа, список кодов у
# главы. Стоит в КАЖДОМ запросе журнала (страница, счёт, люди), чтобы фильтр
# «сотрудник» не стал лазейкой к чужому отделу.
_JOURNAL_WHERE = """
     WHERE (%(scope)s IS NULL OR r.department_code = ANY(%(scope)s))
       AND (%(date_from)s IS NULL OR r.created_at >= %(date_from)s)
       AND (%(date_to)s   IS NULL OR r.created_at <  %(date_to)s)
       AND (%(outcomes)s IS NULL OR r.outcome = ANY(%(outcomes)s))
       AND (%(user_id)s IS NULL OR r.user_id = %(user_id)s)
       AND (%(department_code)s IS NULL OR r.department_code = %(department_code)s)
       AND (%(iin_prefix)s IS NULL OR r.iin LIKE %(iin_prefix)s)
"""

_JOURNAL_PAGE_SQL = """
    SELECT r.id, r.user_id, r.user_name, r.user_role, r.department_code,
           r.iin, r.outcome, r.vendor_message, r.link_issued, r.link_host,
           r.error_text, r.latency_ms, r.ip_address, r.created_at
      FROM sign_link_requests r
""" + _JOURNAL_WHERE + """
     ORDER BY r.created_at DESC, r.id DESC
     LIMIT %(limit)s OFFSET %(offset)s
"""

# Счёт выборки — отдельным агрегатом, а не COUNT(*) OVER (): оконный счётчик
# обнуляется на странице за последней строкой, и пагинация начинает врать
# (та же ловушка, что чинили в разделе «Сессии»).
_JOURNAL_COUNT_SQL = """
    SELECT count(*),
           count(*) FILTER (WHERE r.outcome = 'link'),
           count(*) FILTER (WHERE r.outcome = 'no_documents'),
           count(*) FILTER (WHERE r.outcome NOT IN ('link', 'no_documents')),
           count(DISTINCT r.user_id),
           count(DISTINCT r.iin) FILTER (WHERE r.outcome = 'link')
      FROM sign_link_requests r
""" + _JOURNAL_WHERE

_JOURNAL_PEOPLE_SQL = """
    SELECT r.user_id, max(r.user_name), count(*)
      FROM sign_link_requests r
     WHERE r.user_id IS NOT NULL
       AND (%(scope)s IS NULL OR r.department_code = ANY(%(scope)s))
     GROUP BY r.user_id
     ORDER BY count(*) DESC, max(r.user_name)
     LIMIT 200
"""


def _journal_params(filters):
    scope = filters.get('scope')
    iin = ''.join(ch for ch in str(filters.get('iin') or '') if ch.isdigit())
    return {
        'scope': list(scope) if scope is not None else None,
        'date_from': filters.get('date_from'),
        'date_to': filters.get('date_to'),
        'outcomes': list(filters['outcomes']) if filters.get('outcomes') else None,
        'user_id': filters.get('user_id'),
        'department_code': filters.get('department_code') or None,
        # Префикс, а не «содержит»: ИИН ищут с начала, а по индексу LIKE 'abc%'
        # идёт без полного скана.
        'iin_prefix': (iin + '%') if iin else None,
        'limit': int(filters.get('limit') or 50),
        'offset': int(filters.get('offset') or 0),
    }


def _row(row):
    return {
        'id': row[0],
        'user_id': row[1],
        'user_name': row[2],
        'user_role': row[3],
        'department_code': row[4],
        'iin': row[5],
        'outcome': row[6],
        'vendor_message': row[7],
        'link_issued': bool(row[8]),
        'link_host': row[9],
        'error_text': row[10],
        'latency_ms': row[11],
        'ip_address': row[12],
        'created_at': row[13].isoformat() if row[13] else None,
    }


def journal_page(cursor, filters):
    """Страница журнала + сводка по ВСЕЙ выборке (а не по странице)."""
    params = _journal_params(filters)
    cursor.execute(_JOURNAL_PAGE_SQL, params)
    items = [_row(row) for row in cursor.fetchall()]
    cursor.execute(_JOURNAL_COUNT_SQL, params)
    # Агрегат в Postgres строку возвращает всегда, но распаковка вслепую делает
    # из пустого ответа 500 вместо пустого журнала.
    total, links, no_documents, failures, people, drivers = (
        cursor.fetchone() or (0, 0, 0, 0, 0, 0))
    return {
        'items': items,
        'total': int(total or 0),
        'summary': {
            'requests': int(total or 0),
            'links': int(links or 0),
            'no_documents': int(no_documents or 0),
            'failures': int(failures or 0),
            'people': int(people or 0),
            'drivers': int(drivers or 0),
        },
    }


def journal_people(cursor, scope=None):
    """Кого вообще видел журнал — для выпадающего фильтра.

    Берём из журнала, а не из справочника сотрудников: в фильтре должны стоять
    те, по кому есть что смотреть, включая уже уволенных.
    """
    cursor.execute(_JOURNAL_PEOPLE_SQL, {'scope': list(scope) if scope is not None else None})
    return [{'user_id': row[0], 'name': row[1], 'requests': int(row[2] or 0)}
            for row in cursor.fetchall()]
