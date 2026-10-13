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


# Запрос внешнего сервиса (external.py) — строка того же журнала: вопрос «кому
# выдана ссылка по этому ИИН» обязан находить и его. Сотрудника у такой строки
# нет (user_id NULL), вместо роли стоит API_ROLE, вместо имени — «Внешний
# сервис» с отпечатком ключа. Отдела тоже нет, поэтому главе отдела такие строки
# не видны (граница scope в _JOURNAL_WHERE) — только глобальному админу.
# Двойник кода роли — API_ROLE в signLinkMeta.js.
#
# Строкой от сервиса пишется не всё: выданная ссылка, отказ и опечатка. Ответ
# «документов нет» идёт только в счёт суток (sign_link_api_usage) — см. schema.
API_ROLE = 'api'
API_CLIENT_NAME = 'Внешний сервис (ключ %s)'

# Отбор журнала «Источник»: кто спрашивал — сотрудники или внешний сервис.
SOURCE_STAFF = 'staff'
SOURCE_API = 'api'
SOURCES = (SOURCE_STAFF, SOURCE_API)


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


# Пачка внешнего входа — одним INSERT: сотня строк по одной была бы сотней
# обращений к базе. Массивы, а не execute_values: тот шлёт страницы по сто
# строк отдельными запросами и врёт про rowcount.
_API_INSERT_SQL = """
    INSERT INTO sign_link_requests
        (user_id, user_name, user_role, department_id, department_code,
         iin, outcome, vendor_message, link_issued, link_host, error_text,
         latency_ms, ip_address, user_agent)
    SELECT NULL::integer, %(user_name)s, %(user_role)s, NULL::integer, NULL,
           t.iin, t.outcome, t.vendor_message, t.link_issued, t.link_host,
           t.error_text, t.latency_ms, %(ip)s, %(ua)s
      FROM unnest(%(iin)s::text[], %(outcome)s::text[], %(vendor_message)s::text[],
                  %(link_issued)s::boolean[], %(link_host)s::text[],
                  %(error_text)s::text[], %(latency_ms)s::integer[])
           AS t(iin, outcome, vendor_message, link_issued, link_host, error_text,
                latency_ms)
"""


def log_api_requests(cursor, *, key_mark, rows, ip_address=None, user_agent=None):
    """Строки журнала за одну пачку внешнего входа. Возвращает их число.

    rows — словари: iin и outcome обязательны; vendor_message, link_issued,
    link_host, error_text, latency_ms — по исходу. Самой ссылки среди них нет,
    как и у запросов сотрудников: в журнале только факт и домен.
    """
    rows = list(rows or [])
    if not rows:
        return 0
    cursor.execute(_API_INSERT_SQL, {
        'user_name': API_CLIENT_NAME % key_mark,
        'user_role': API_ROLE,
        'ip': (ip_address or '')[:64] or None,
        'ua': (user_agent or '')[:500] or None,
        'iin': [str(row.get('iin') or '')[:32] for row in rows],
        'outcome': [row['outcome'] for row in rows],
        'vendor_message': [(row.get('vendor_message') or '')[:1000] or None for row in rows],
        'link_issued': [bool(row.get('link_issued')) for row in rows],
        'link_host': [(row.get('link_host') or '')[:120] or None for row in rows],
        'error_text': [(row.get('error_text') or '')[:1000] or None for row in rows],
        'latency_ms': [int(row['latency_ms']) if row.get('latency_ms') is not None else None
                       for row in rows],
    })
    return len(rows)


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


# У внешнего входа предел один на всех держателей ключей и считает КАЖДЫЙ
# принятый ИИН, а не только походы к генератору: и «документов нет», и опечатку —
# иначе утёкший ключ гонял бы генератор и журнал, не тратя предела. Счёт лежит
# в sign_link_api_usage, строкой на ключ в сутки: прочитать предел — одна-две
# строки, а не подсчёт по журналу на каждый запрос.
_API_USED_TODAY_SQL = """
    SELECT COALESCE(sum(u.iins), 0)
      FROM sign_link_api_usage u
     WHERE u.day = %(day)s
"""

_API_USAGE_ADD_SQL = """
    INSERT INTO sign_link_api_usage AS u
        (day, key_mark, requests, iins, links, not_found, invalid, errors)
    VALUES (%(day)s, %(key_mark)s, 1, %(iins)s, %(links)s, %(not_found)s,
            %(invalid)s, %(errors)s)
    ON CONFLICT (day, key_mark) DO UPDATE
       SET requests = u.requests + 1,
           iins = u.iins + EXCLUDED.iins,
           links = u.links + EXCLUDED.links,
           not_found = u.not_found + EXCLUDED.not_found,
           invalid = u.invalid + EXCLUDED.invalid,
           errors = u.errors + EXCLUDED.errors,
           updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
"""


def api_used_today(cursor):
    """Сколько ИИН внешний вход принял сегодня, по всем ключам (сутки — по Алматы)."""
    cursor.execute(_API_USED_TODAY_SQL, {'day': now_almaty().date()})
    row = cursor.fetchone()
    return int(row[0] or 0) if row else 0


def add_api_usage(cursor, *, key_mark, links=0, not_found=0, invalid=0, errors=0):
    """Записать пачку в счёт суток. Возвращает число принятых ИИН.

    Принятый ИИН — тот, по которому что-то сделано: спросили генератор (ссылка,
    «документов нет», отказ, сбой) либо отвергли как опечатку. ИИН, до которого
    очередь не дошла, в счёт не идёт: его пришлют снова.
    """
    counts = {'links': int(links), 'not_found': int(not_found), 'invalid': int(invalid),
              'errors': int(errors)}
    accepted = sum(counts.values())
    cursor.execute(_API_USAGE_ADD_SQL, dict(counts, day=now_almaty().date(),
                                            key_mark=str(key_mark)[:16], iins=accepted))
    return accepted


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
       AND (%(api_rows)s IS NULL
            OR (r.user_role IS NOT DISTINCT FROM %(api_role)s) = %(api_rows)s)
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
        # Источник: None — все строки, True — только внешний сервис, False —
        # только сотрудники.
        'api_rows': {SOURCE_API: True, SOURCE_STAFF: False}.get(filters.get('source')),
        'api_role': API_ROLE,
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


_JOURNAL_HAS_API_ROWS_SQL = """
    SELECT EXISTS (
        SELECT 1 FROM sign_link_requests r
         WHERE r.user_id IS NULL AND r.user_role = %(api_role)s
    )
"""


def journal_has_api_rows(cursor):
    """Есть ли в журнале запросы внешнего сервиса — показывать ли отбор «Источник».

    По всему журналу, а не по выборке: отбор не должен пропадать оттого, что
    им же и отсеяли такие строки.
    """
    cursor.execute(_JOURNAL_HAS_API_ROWS_SQL, {'api_role': API_ROLE})
    row = cursor.fetchone()
    return bool(row and row[0])
