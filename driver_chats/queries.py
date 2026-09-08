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


# Поиск по ХВОСТУ номера — запасной ход для иностранных номеров и для любой
# записи, которой нет среди точных вариантов.
#
# Зачем: точные варианты перебирают то, как номер пишут У НАС («8…», «+7…», без
# кода страны). Кодов стран две сотни, и достроить турецкий «531 729 83 61» до
# «90 531 729 83 61» нельзя ничем, кроме гадания. А хвост у номера один и тот же
# в любой записи.
#
# Выражение в WHERE — ДОСЛОВНО то же, что в индексе idx_c2d_requests_phone_tail
# (driver_chats/schema.py): разойдись они хоть пробелом, планировщик индекс не
# возьмёт и уйдёт в полный проход по 86 тыс. строк (замер: 0,2 с против единиц
# миллисекунд).
#
# LIMIT 3, а не 1: по одному хвосту НЕ должно находиться двух разных водителей
# (проверено на всей базе — 16 421 хвост, ни одного совпадения), но если это
# однажды случится, раздел обязан заметить и спросить номер целиком, а не
# показать переписку соседа.
_LOCAL_CLIENT_BY_TAIL_SQL = r"""
    SELECT client_id, min(client_phone)
      FROM c2d_requests
     WHERE right(regexp_replace(client_phone, '\D', '', 'g'), 9) = %(tail)s
       AND client_id IS NOT NULL
       -- Только то, что и правда номер. В этой же колонке вендор держит
       -- идентификаторы WhatsApp («[wa_gupshup] KZ.926590227191272», 852
       -- строки): у них тоже есть девять последних цифр, и без этого условия
       -- поиск мог бы выдать за водителя чужой диалог мессенджера.
       AND client_phone ~ '^[+]?[0-9]{10,15}$'
     GROUP BY client_id
     LIMIT 3
"""


def local_client_by_tail(cursor, tail):
    """(client_id, телефон_как_в_базе) по хвосту номера.

    Возвращает:
        None                — не нашли;
        (client_id, phone)  — нашли ровно одного;
        ('ambiguous', None) — хвост делят несколько водителей.

    Телефон отдаём В ТОЙ ЗАПИСИ, в какой он лежит у нас: под ней потом
    сохраняется кеш и пишется журнал, и повторный поиск того же водителя
    попадает уже в точный путь, без второго прохода по хвосту.
    """
    if not tail:
        return None
    cursor.execute(_LOCAL_CLIENT_BY_TAIL_SQL, {'tail': str(tail)})
    rows = [r for r in cursor.fetchall() if r and r[0] is not None]
    if not rows:
        return None
    if len(rows) > 1:
        return ('ambiguous', None)
    return (int(rows[0][0]), rows[0][1])


# Справочник таксопарков. Канал в Chat2Desk = парк, на чей номер написал
# водитель, и оператору он нужен первым делом: по одному и тому же телефону
# приходят чаты разных парков.
#
# Берём из своей базы, а не из вендора: `c2d_requests` наполняется ночным синком
# метрик бесплатно и держит 45 дней, а это 14 парков — весь живой трафик.
# Вендорский `/v1/channels` остаётся запасным (и неполным: 15 записей из 23,
# offset он игнорирует).
_CHANNELS_SQL = """
    SELECT channel_id, max(channel_name)
      FROM c2d_requests
     WHERE channel_id IS NOT NULL
       AND COALESCE(NULLIF(TRIM(channel_name), ''), '') <> ''
     GROUP BY channel_id
"""


# Справочник парков держим в процессе. Запрос бесплатный по деньгам, но не по
# времени: индекса под него нет и быть не может — это агрегат по всей таблице,
# Seq Scan по 86 052 строкам ради 14 значений, 45 мс на КАЖДОМ поиске и
# обновлении (замер на проде 07.09.2026). Парков 14 и новый появляется раз в
# месяцы, а только что подключённый и так подхватится: неизвестный канал
# добирается у вендора отдельной веткой в /search.
_CHANNELS_CACHE = {'names': None, 'at': None}
_CHANNELS_TTL = timedelta(minutes=30)


def channel_names(cursor):
    """id канала -> название таксопарка. Ноль вызовов API."""
    at = _CHANNELS_CACHE.get('at')
    if _CHANNELS_CACHE.get('names') is not None and at:
        if now_almaty() - at < _CHANNELS_TTL:
            return _CHANNELS_CACHE['names']
    cursor.execute(_CHANNELS_SQL)
    names = {int(row[0]): row[1] for row in cursor.fetchall() if row[0] is not None}
    _CHANNELS_CACHE['names'] = names
    _CHANNELS_CACHE['at'] = now_almaty()
    return names


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
    """Свежий кеш переписки и время его снятия — или (None, None).

    Кеш существует ради квоты вендора и ради скорости: смена целиком открывает
    один и тот же чат несколько раз подряд, пока водитель на линии. TTL короткий
    (минуты): переписка живая, и показать оператору вчерашнее состояние диалога,
    который идёт прямо сейчас, — хуже, чем сходить в API ещё раз.

    Время снятия отдаём вместе с сообщениями, потому что экран подписывает ленту
    «обновлено в HH:MM». Взять эту минуту с часов браузера значило бы соврать
    ровно на возраст кеша — до пяти минут на разговоре, который идёт прямо
    сейчас, — и обесценить кнопку «Обновить»: подпись менялась бы, а лента нет.
    """
    cursor.execute(_CACHE_GET_SQL, {'client_id': int(client_id)})
    row = cursor.fetchone()
    if not row:
        return None, None
    messages, fetched_at, cached_from, cached_to = row
    if not fetched_at:
        return None, None
    # Окно сдвинулось (наступил новый день) — кеш больше не про то, что просят.
    if cached_from != window_from or cached_to != window_to:
        return None, None
    if (now_almaty() - fetched_at).total_seconds() > max(0, int(ttl_seconds)):
        return None, None
    return (messages or []), fetched_at


_CACHED_CLIENT_SQL = """
    SELECT client_id
      FROM dch_message_cache
     WHERE phone = %(phone)s
     ORDER BY fetched_at DESC
     LIMIT 1
"""


def cached_client_id(cursor, phone):
    """client_id по телефону из СВОЕЙ памяти. Ноль вызовов API, один индекс.

    Зачем отдельно от local_client_id: тот ищет по c2d_requests, которую
    наполняет НОЧНОЙ синк («в 04:10 за вчера»). Водителя, который написал
    впервые сегодня, там нет — и каждый поиск по нему, включая «Обновить»,
    уходил к вендору перебирать записи номера: до трёх вызовов, ~360 мс. А наша
    собственная таблица эту пару узнала ещё на первом поиске.

    Срок жизни у ПАРЫ телефон-клиент не тот, что у сообщений: сообщения
    протухают за пять минут, а клиент за номером закреплён навсегда. Поэтому
    читаем строку без оглядки на fetched_at.
    """
    if not phone:
        return None
    cursor.execute(_CACHED_CLIENT_SQL, {'phone': phone})
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else None


_PENDING_NOTES_SQL = """
    SELECT c2d_message_id, comment_text, channel_id, dialog_id, request_id, created_at
      FROM dch_events
     WHERE kind = 'handoff'
       AND client_id = %(client_id)s
       AND c2d_message_id IS NOT NULL
       AND created_at >= {now} - make_interval(mins => %(minutes)s)
     ORDER BY created_at
""".format(now=_NOW)


def pending_handoff_notes(cursor, client_id, minutes):
    """Заметки «Передан», отправленные только что. Ноль вызовов API.

    Вендор показывает свежесозданное сообщение в /v1/messages примерно через
    минуту (замер 07.09.2026), и всё это время оператор не видит собственный
    комментарий. Журнал же знает про него сразу: там лежит и id, который вернул
    вендор, и точный текст, и адрес чата. Отсюда лента и добирает недостающее.

    Окно намеренно шире наблюдавшегося запаздывания: лишняя заметка ничего не
    портит — доехавшая копия вендора вытеснит её по совпадению id.
    """
    cursor.execute(_PENDING_NOTES_SQL,
                   {'client_id': int(client_id), 'minutes': int(minutes)})
    return [{
        'message_id': row[0],
        'text': row[1] or '',
        'channel_id': row[2],
        'dialog_id': row[3],
        'request_id': row[4],
        'created': row[5],
    } for row in cursor.fetchall()]


def drop_cached_messages(cursor, client_id):
    """Пометить кеш переписки протухшим.

    Зовётся сразу после «Передан»: заметка уже лежит в чате у вендора, но наш
    снимок ей на пять минут старше. Без сброса оператор жмёт «Найти» и НЕ видит
    собственный комментарий — выглядит это как «кнопка не сработала», и человек
    жмёт её второй раз, а отозвать лишнюю заметку через API нельзя.

    Не DELETE, а состаривание: строка держит ещё и пару телефон-клиент
    (cached_client_id), которая к свежести сообщений отношения не имеет. Удалив
    строку, мы бы после каждого «Передан» заставляли следующий же поиск заново
    искать клиента у вендора — то есть тормозили ровно тот случай, ради
    которого кнопку «Обновить» и жмут. Уборщик раздела снимет строку сам, когда
    она выйдет за двухсуточное окно.
    """
    cursor.execute(
        "UPDATE dch_message_cache "
        "   SET fetched_at = %(stale)s "
        " WHERE client_id = %(client_id)s",
        {'client_id': int(client_id), 'stale': datetime(1970, 1, 1)})
    return cursor.rowcount or 0


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
         channel_id, dialog_id, request_id, channel_name, comment_text,
         c2d_message_id, messages_count, ip_address, user_agent)
    VALUES
        (%(kind)s, %(user_id)s, %(user_name)s, %(user_role)s, %(department_id)s,
         %(phone)s, %(client_id)s, %(channel_id)s, %(dialog_id)s, %(request_id)s,
         %(channel_name)s, %(comment_text)s, %(c2d_message_id)s, %(messages_count)s,
         %(ip)s, %(ua)s)
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
        'channel_id': fields.get('channel_id'),
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
           e.client_id, e.channel_id, e.dialog_id, e.request_id, e.channel_name,
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
        'channel_id': row[7],
        'dialog_id': row[8],
        'request_id': row[9],
        'channel_name': row[10],
        'comment_text': row[11],
        'c2d_message_id': row[12],
        'messages_count': row[13],
        'ip_address': row[14],
        'created_at': row[15].isoformat() if row[15] else None,
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
