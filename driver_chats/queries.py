"""SQL-слой раздела «Чаты водителей».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют ни
пулом, ни транзакцией — их держит вызывающий. Так же устроены wiki, crm, parcels
и call_qa.

Почему это важно именно здесь: кнопка «Передан» отправляет заметку в чужую
систему и пишет строку в журнал. Заметку отозвать нельзя (метода DELETE у
messages вендор не даёт), поэтому запись в журнал обязана лечь в ту же
транзакцию, что и всё остальное действие: «передал, но в журнале нет» — это
ровно та дыра, ради закрытия которой журнал и просили.
"""

import json
from datetime import datetime, timedelta

from . import access

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

_ALMATY_OFFSET = timedelta(hours=5)


def now_almaty():
    return datetime.utcnow() + _ALMATY_OFFSET


# ─────────────────────────────────────────────────────────────────────────────
# Контекст доступа
# ─────────────────────────────────────────────────────────────────────────────
#
# Направление берём вместе с моделью расчёта: по ней (а не по id и не по имени)
# опознаётся чат-менеджер, которому раздел закрыт. Направления версионируются —
# id 69 лишь текущая версия «Чат менеджера», — а переименование направления
# обнуляет привязку операторов. Модель переживает и то, и другое.

_ACCESS_CONTEXT_SQL = """
WITH me AS (
    SELECT id, name, role, department_id, direction_id, status
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
    (SELECT dir.calculation_model_code FROM directions dir
      WHERE dir.id = (SELECT direction_id FROM me)),
    (SELECT status        FROM me),
    COALESCE((SELECT array_agg(id)   FROM headed), '{}'),
    COALESCE((SELECT array_agg(code) FROM headed), '{}')
"""


def load_access_context(cursor, user_id):
    """Профиль + периметр одним запросом."""
    cursor.execute(_ACCESS_CONTEXT_SQL, {'user_id': int(user_id)})
    row = cursor.fetchone()
    if not row or row[1] is None:
        return None
    name, role, department_id, department_code, direction_model, status, headed, headed_codes = row
    return {
        'user_id': int(user_id),
        'name': name,
        'role': access.normalize_role(role),
        'department_id': department_id,
        'department_code': department_code,
        'direction_model': direction_model,
        'status': status,
        'headed_department_ids': list(headed or []),
        'headed_department_codes': list(headed_codes or []),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Мост «телефон -> клиент Chat2Desk» по своей базе
# ─────────────────────────────────────────────────────────────────────────────
#
# Телефон уже лежит в c2d_requests: его заполняет ежедневный синк метрик теми же
# строками, что идут в chat_manager_daily_metrics, — то есть БЕСПЛАТНО, без
# отдельного вызова API. Заполнен он у 100 % строк (85 222 из 85 222), а связь
# «телефон -> client_id» однозначна: ни у одного номера за 45 дней нет двух
# разных клиентов (проверено запросом с HAVING count(DISTINCT client_id) > 1).
#
# Ретеншн таблицы 45 дней, поэтому мост находится примерно для 61 % номеров;
# остальным (тем, кто пишет впервые) client_id добирается одним вызовом
# /v1/clients?phone=. Это единственное место, где раздел вообще может потратить
# квоту на ПОИСК.

_LOCAL_CLIENT_SQL = """
    SELECT client_id
      FROM c2d_requests
     WHERE client_phone = ANY(%(variants)s)
       AND client_id IS NOT NULL
     ORDER BY day DESC
     LIMIT 1
"""


def local_client_id(cursor, variants):
    """client_id по телефону из своей базы. Ноль вызовов API."""
    if not variants:
        return None
    cursor.execute(_LOCAL_CLIENT_SQL, {'variants': list(variants)})
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else None


# Метаданные заявок — канал, чат-менеджер, оценка водителя. Живут в той же
# бесплатной таблице. Сегодняшних заявок в ней нет (синк идёт в 04:10 за вчера),
# поэтому обогащение НЕОБЯЗАТЕЛЬНОЕ: чат показывается и без него, просто без
# названия канала и оценки.
_REQUEST_META_SQL = """
    SELECT request_id, channel_name, transport, c2d_operator_name,
           request_type, rating_score, rating_text, client_name
      FROM c2d_requests
     WHERE request_id = ANY(%(ids)s)
"""


def request_meta(cursor, request_ids):
    """request_id -> метаданные заявки. Пустой словарь, если данных ещё нет."""
    ids = [int(rid) for rid in (request_ids or []) if rid is not None]
    if not ids:
        return {}
    cursor.execute(_REQUEST_META_SQL, {'ids': ids})
    out = {}
    for row in cursor.fetchall():
        out[int(row[0])] = {
            'channel_name': row[1],
            'transport': row[2],
            'operator_name': row[3],
            'request_type': row[4],
            'rating_score': row[5],
            'rating_text': row[6],
            'client_name': row[7],
        }
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Кеш переписки
# ─────────────────────────────────────────────────────────────────────────────

_CACHE_GET_SQL = """
    SELECT messages, fetched_at, window_from, window_to
      FROM dch_message_cache
     WHERE client_id = %(client_id)s
"""

_CACHE_PUT_SQL = """
    INSERT INTO dch_message_cache
        (client_id, phone, messages, messages_count, window_from, window_to, fetched_at)
    VALUES
        (%(client_id)s, %(phone)s, %(messages)s, %(count)s, %(window_from)s, %(window_to)s, {now})
    ON CONFLICT (client_id) DO UPDATE SET
        phone = EXCLUDED.phone,
        messages = EXCLUDED.messages,
        messages_count = EXCLUDED.messages_count,
        window_from = EXCLUDED.window_from,
        window_to = EXCLUDED.window_to,
        fetched_at = EXCLUDED.fetched_at
""".format(now=_NOW)


def cached_messages(cursor, client_id, window_from, window_to, ttl_seconds):
    """Свежий кеш переписки или None.

    Кеш существует ради квоты вендора и ради скорости: смена целиком открывает
    один и тот же чат несколько раз подряд, пока водитель на линии. TTL короткий
    (минуты): переписка живая, и показать оператору вчерашнее состояние диалога,
    который идёт прямо сейчас, — хуже, чем сходить в API ещё раз.
    """
    cursor.execute(_CACHE_GET_SQL, {'client_id': int(client_id)})
    row = cursor.fetchone()
    if not row:
        return None
    messages, fetched_at, cached_from, cached_to = row
    if not fetched_at:
        return None
    # Окно сдвинулось (наступил новый день) — кеш больше не про то, что просят.
    if cached_from != window_from or cached_to != window_to:
        return None
    if (now_almaty() - fetched_at).total_seconds() > max(0, int(ttl_seconds)):
        return None
    return messages or []


def store_messages(cursor, client_id, phone, messages, window_from, window_to):
    cursor.execute(_CACHE_PUT_SQL, {
        'client_id': int(client_id),
        'phone': phone,
        'messages': json.dumps(messages, ensure_ascii=False),
        'count': len(messages or []),
        'window_from': window_from,
        'window_to': window_to,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Журнал
# ─────────────────────────────────────────────────────────────────────────────

_EVENT_INSERT_SQL = """
    INSERT INTO dch_events
        (kind, user_id, user_name, user_role, department_id, phone, client_id,
         dialog_id, request_id, channel_name, comment_text, c2d_message_id,
         messages_count, ip_address, user_agent)
    VALUES
        (%(kind)s, %(user_id)s, %(user_name)s, %(user_role)s, %(department_id)s,
         %(phone)s, %(client_id)s, %(dialog_id)s, %(request_id)s, %(channel_name)s,
         %(comment_text)s, %(c2d_message_id)s, %(messages_count)s, %(ip)s, %(ua)s)
    RETURNING id, created_at
"""


def log_event(cursor, ctx, kind, **fields):
    """Строка журнала. Снимок человека берётся из контекста, а не джойнится потом.

    Журнал отвечает на вопрос «кто это сделал ТОГДА»: человек меняет отдел,
    увольняется, роль ему заменяют назначением главой отдела. Джойн к users в
    момент чтения показал бы сегодняшнее состояние, а не то, что было.
    """
    cursor.execute(_EVENT_INSERT_SQL, {
        'kind': kind,
        'user_id': int(ctx['user_id']),
        'user_name': ctx.get('name'),
        'user_role': ctx.get('role'),
        'department_id': ctx.get('department_id'),
        'phone': fields.get('phone'),
        'client_id': fields.get('client_id'),
        'dialog_id': fields.get('dialog_id'),
        'request_id': fields.get('request_id'),
        'channel_name': fields.get('channel_name'),
        'comment_text': fields.get('comment_text'),
        'c2d_message_id': fields.get('c2d_message_id'),
        'messages_count': fields.get('messages_count'),
        'ip': fields.get('ip_address'),
        'ua': (fields.get('user_agent') or '')[:500] or None,
    })
    row = cursor.fetchone()
    return {'id': row[0], 'created_at': row[1].isoformat() if row and row[1] else None}


_JOURNAL_WHERE = """
     WHERE (%(date_from)s IS NULL OR e.created_at >= %(date_from)s)
       AND (%(date_to)s   IS NULL OR e.created_at <  %(date_to)s)
       AND (%(kinds)s IS NULL OR e.kind = ANY(%(kinds)s))
       AND (%(user_id)s IS NULL OR e.user_id = %(user_id)s)
       AND (%(phone)s IS NULL OR e.phone = %(phone)s)
"""

_JOURNAL_PAGE_SQL = """
    SELECT e.id, e.kind, e.user_id, e.user_name, e.user_role, e.phone,
           e.client_id, e.dialog_id, e.request_id, e.channel_name,
           e.comment_text, e.c2d_message_id, e.messages_count,
           e.ip_address, e.created_at
      FROM dch_events e
""" + _JOURNAL_WHERE + """
     ORDER BY e.created_at DESC, e.id DESC
     LIMIT %(limit)s OFFSET %(offset)s
"""

# Счёт выборки — отдельным агрегатом, а не COUNT(*) OVER (). Оконный счётчик
# обнуляется на странице за последней строкой, и пагинация начинает врать
# (та же ловушка, что чинили в разделе «Сессии»).
_JOURNAL_COUNT_SQL = """
    SELECT count(*),
           count(*) FILTER (WHERE e.kind = 'handoff'),
           count(DISTINCT e.user_id),
           count(DISTINCT e.phone)
      FROM dch_events e
""" + _JOURNAL_WHERE


def _journal_params(filters):
    return {
        'date_from': filters.get('date_from'),
        'date_to': filters.get('date_to'),
        'kinds': list(filters['kinds']) if filters.get('kinds') else None,
        'user_id': filters.get('user_id'),
        'phone': filters.get('phone'),
        'limit': int(filters.get('limit') or 50),
        'offset': int(filters.get('offset') or 0),
    }


def _event_row(row):
    return {
        'id': row[0],
        'kind': row[1],
        'user_id': row[2],
        'user_name': row[3],
        'user_role': row[4],
        'phone': row[5],
        'client_id': row[6],
        'dialog_id': row[7],
        'request_id': row[8],
        'channel_name': row[9],
        'comment_text': row[10],
        'c2d_message_id': row[11],
        'messages_count': row[12],
        'ip_address': row[13],
        'created_at': row[14].isoformat() if row[14] else None,
    }


def journal_page(cursor, filters):
    """Страница журнала + сводка по ВСЕЙ выборке (а не по странице)."""
    params = _journal_params(filters)
    cursor.execute(_JOURNAL_PAGE_SQL, params)
    items = [_event_row(row) for row in cursor.fetchall()]
    cursor.execute(_JOURNAL_COUNT_SQL, params)
    # Агрегат в Postgres строку возвращает всегда, но распаковка вслепую делает
    # из пустого ответа 500 вместо пустого журнала. Раздел новый — пусть он
    # честно показывает «действий не было», а не внутреннюю ошибку.
    total, handoffs, people, drivers = cursor.fetchone() or (0, 0, 0, 0)
    return {
        'items': items,
        'total': int(total or 0),
        'summary': {
            'events': int(total or 0),
            'handoffs': int(handoffs or 0),
            'people': int(people or 0),
            'drivers': int(drivers or 0),
        },
    }


def journal_all(cursor, filters, cap=20000):
    """Вся выборка для выгрузки. Потолок — чтобы книга не съела память инстанса."""
    params = _journal_params(filters)
    params['limit'] = int(cap)
    params['offset'] = 0
    cursor.execute(_JOURNAL_PAGE_SQL, params)
    return [_event_row(row) for row in cursor.fetchall()]


_JOURNAL_PEOPLE_SQL = """
    SELECT e.user_id, max(e.user_name), count(*)
      FROM dch_events e
     GROUP BY e.user_id
     ORDER BY count(*) DESC
     LIMIT 200
"""


def journal_people(cursor):
    """Кого вообще видел журнал — для выпадающего фильтра.

    Берём из журнала, а не из справочника сотрудников: в фильтре должны стоять
    те, по кому есть что смотреть, включая уже уволенных.
    """
    cursor.execute(_JOURNAL_PEOPLE_SQL)
    return [{'user_id': row[0], 'name': row[1], 'events': int(row[2] or 0)}
            for row in cursor.fetchall()]
