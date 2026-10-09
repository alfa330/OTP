"""Тесты по дням и сами звонки и чаты с номерами реестра — для экрана раздела.

Источники (те же таблицы, из которых считают табло и отчёты, — только здесь
номера реестра, наоборот, ВЫБИРАЮТСЯ):

    freepbx    звонки ОП — касания FreePBX (cdr_touches), номер — десять цифр
    oktell     звонки СЗоВ — живой запрос к Oktell (Call_Systems_hst), один на период
    binotel    звонки Тез КЦ — зеркало журнала Binotel (tez_lead_calls)
    wazzup     WhatsApp ОП — Верификаторы и Поток (wazzup_messages, 45 дней)
    chat2desk  чаты СЗоВ — обращения Chat2Desk (c2d_requests, 45 дней)
    chatapp    WhatsApp Тез КЦ (chatapp_messages, 45 дней)

Один тест — один звонок или одна переписка за сутки: тестировщик, написавший в
чат утром и вечером, сделал один тест, а не двадцать сообщений.

Источник, который не ответил (Oktell лежит, таблицы нет на стенде), не роняет
экран: его тесты не показываются, а в ответе есть строка «чего не хватает».
"""

import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone

from . import keys as keys_mod

ALMATY = timezone(timedelta(hours=5))

# Потолок периода: Oktell читается живым запросом на весь период, а переписка
# хранится 45 дней — больше месяца экран всё равно честно не покажет.
MAX_DAYS = 31

# Прокси Oktell режет ответ на 1000 строк. Тестов с десятка номеров за месяц
# столько не бывает; упрёмся — экран скажет, что список неполный.
OKTELL_ROW_CAP = 1000

SOURCES = {
    'freepbx': {'kind': 'call', 'label': 'Звонок · ОП'},
    'oktell': {'kind': 'call', 'label': 'Звонок · СЗоВ'},
    'binotel': {'kind': 'call', 'label': 'Звонок · Тез КЦ'},
    'wazzup': {'kind': 'chat', 'label': 'WhatsApp · ОП'},
    'chat2desk': {'kind': 'chat', 'label': 'Чат · СЗоВ'},
    'chatapp': {'kind': 'chat', 'label': 'WhatsApp · Тез КЦ'},
}

_WAZZUP_ACCOUNTS = {'op': 'Верификаторы', 'potok': 'Поток'}

# Итог звонка Binotel приходит кодом вендора — на экран словами.
_BINOTEL_RESULTS = {
    'ANSWER': 'Отвечен', 'VM-SUCCESS': 'Отвечен', 'SUCCESS': 'Отвечен', 'TRANSFER': 'Переведён',
    'NOANSWER': 'Не ответил', 'BUSY': 'Занято', 'CANCEL': 'Сброшен', 'CONGESTION': 'Не прошёл',
    'CHANUNAVAIL': 'Недоступен', 'VM': 'Голосовая почта', 'ONLINE': 'Идёт сейчас',
}


class PeriodError(ValueError):
    """Период не годится: текст — для человека."""


def parse_period(day_from, day_to, today):
    """Строки ?from=&to= → (date, date). Пусто — последние 14 дней по сегодня."""
    try:
        end = date.fromisoformat(day_to) if day_to else today
        start = date.fromisoformat(day_from) if day_from else end - timedelta(days=13)
    except (TypeError, ValueError):
        raise PeriodError('Период не разобран — выберите даты заново') from None
    if start > end:
        raise PeriodError('Начало периода позже конца')
    if (end - start).days + 1 > MAX_DAYS:
        raise PeriodError(f'Период — не больше {MAX_DAYS} дней')
    return start, end


def _local(value):
    """Время источника → «часы Алматы» без пояса (для сортировки и дня)."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is not None:
        value = value.astimezone(ALMATY).replace(tzinfo=None)
    return value


def _item(source, phone_key, at, **extra):
    meta = SOURCES[source]
    at = _local(at)
    item = {
        'source': source,
        'kind': meta['kind'],
        'channel': meta['label'],
        'phone_key': phone_key,
        'at': at.isoformat(timespec='seconds') if at else None,
        'day': at.date().isoformat() if at else None,
        'direction': None,
        'operator': None,
        'result': None,
        'duration_seconds': None,
        'messages': None,
    }
    item.update(extra)
    return item


def _rows(cursor):
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Наша база
# ─────────────────────────────────────────────────────────────────────────────

def _freepbx(cursor, phone_keys, start, end):
    # Номер касания — ровно десять цифр (cdr/touches.py: norm_phone), по нему индекс.
    # Сотрудник по внутреннему номеру — нынешний его владелец: номер уволившегося
    # отдают новому, а экрану тестов точная история номера не нужна.
    cursor.execute(
        """
        SELECT t.linkedid, t.phone, t.started_at, t.call_type, t.result, t.ext, t.queue,
               COALESCE(t.talk_measured_seconds, t.talk_seconds) AS talk_seconds,
               (SELECT u.name
                  FROM users u
                  LEFT JOIN operator_profiles op ON op.user_id = u.id
                 WHERE t.ext <> ''
                   AND COALESCE(NULLIF(TRIM(u.sip_number), ''), NULLIF(TRIM(op.sip_number), '')) = t.ext
                 ORDER BY (COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')) DESC, u.id DESC
                 LIMIT 1) AS operator_name
          FROM cdr_touches t
         WHERE t.phone = ANY(%(keys)s)
           AND t.call_day BETWEEN %(start)s AND %(end)s
        """,
        {'keys': list(phone_keys), 'start': start, 'end': end},
    )
    items = []
    for row in _rows(cursor):
        direction = 'out' if row['call_type'] == 'Исходящий' else 'in'
        items.append(_item(
            'freepbx', row['phone'], row['started_at'],
            id=f"freepbx:{row['linkedid']}:{row['phone']}",
            direction=direction,
            operator=row['operator_name'] or (f"вн. {row['ext']}" if row['ext'] else None),
            result=row['result'],
            duration_seconds=int(row['talk_seconds'] or 0),
        ))
    return items


def _binotel(cursor, phone_keys, start, end):
    # Зеркало хранит только казахстанские номера: «7» и десять цифр.
    cursor.execute(
        """
        SELECT c.general_call_id, c.phone_norm, c.started_at, c.call_type, c.billsec,
               c.disposition, c.internal_number,
               COALESCE(u.name, NULLIF(c.employee_name, '')) AS operator_name
          FROM tez_lead_calls c
          LEFT JOIN users u ON u.id = c.operator_id
         WHERE c.phone_norm = ANY(%(phones)s)
           AND c.started_at >= %(ts_from)s AND c.started_at < %(ts_to)s
        """,
        {'phones': ['7' + key for key in phone_keys],
         'ts_from': datetime.combine(start, datetime.min.time(), ALMATY),
         'ts_to': datetime.combine(end + timedelta(days=1), datetime.min.time(), ALMATY)},
    )
    items = []
    for row in _rows(cursor):
        direction = {0: 'in', 1: 'out'}.get(row['call_type'])
        items.append(_item(
            'binotel', keys_mod.phone_key(row['phone_norm']), row['started_at'],
            id=f"binotel:{row['general_call_id']}",
            direction=direction,
            operator=row['operator_name'] or (f"вн. {row['internal_number']}" if row['internal_number'] else None),
            result=_BINOTEL_RESULTS.get(str(row['disposition'] or '').upper(), row['disposition'] or None),
            duration_seconds=int(row['billsec'] or 0),
        ))
    return items


def _wazzup(cursor, phone_keys, start, end):
    # Номер чата: contact_phone, а у WhatsApp, где его нет, — сам chat_id (это и
    # есть номер). Чаты сначала находятся в сводке wazzup_chats (одна строка на
    # чат), сообщения — по индексу (channel_id, chat_id, dt).
    phone_expr = ("COALESCE(NULLIF(c.contact_phone, ''), "
                  "CASE WHEN c.chat_type IN ('whatsapp', 'wapi') THEN c.chat_id END)")
    cursor.execute(
        f"""
        WITH chats AS (
            SELECT c.channel_id, c.chat_id, c.account, {keys_mod.sql_key(phone_expr)} AS phone_key
              FROM wazzup_chats c
             WHERE {keys_mod.sql_key(phone_expr)} = ANY(%(keys)s)
        )
        SELECT ch.account, ch.channel_id, ch.chat_id, ch.phone_key,
               (m.dt AT TIME ZONE 'Asia/Almaty')::date AS day,
               MIN(m.dt) AS first_at,
               COUNT(*) AS messages,
               COUNT(*) FILTER (WHERE NOT m.is_echo) AS inbound,
               string_agg(DISTINCT m.author_name, ', ')
                   FILTER (WHERE m.is_echo AND COALESCE(m.author_name, '') <> '') AS operators
          FROM chats ch
          JOIN wazzup_messages m ON m.channel_id = ch.channel_id AND m.chat_id = ch.chat_id
         WHERE m.dt >= %(ts_from)s AND m.dt < %(ts_to)s
           AND NOT m.is_deleted
         GROUP BY ch.account, ch.channel_id, ch.chat_id, ch.phone_key, 5
        """,
        {'keys': list(phone_keys),
         'ts_from': datetime.combine(start, datetime.min.time(), ALMATY),
         'ts_to': datetime.combine(end + timedelta(days=1), datetime.min.time(), ALMATY)},
    )
    items = []
    for row in _rows(cursor):
        account = _WAZZUP_ACCOUNTS.get(row['account'], row['account'])
        items.append(_item(
            'wazzup', row['phone_key'], row['first_at'],
            id=f"wazzup:{row['channel_id']}:{row['chat_id']}:{row['day'].isoformat()}",
            direction='in' if row['inbound'] else 'out',
            operator=row['operators'],
            messages=int(row['messages'] or 0),
            note=account,
        ))
    return items


def _chat2desk(cursor, phone_keys, start, end):
    # У клиента WhatsApp, пришедшего идентификатором, в client_phone лежит
    # «[wa_…] KZ.…», а номер — в assigned_phone: смотреть надо оба поля.
    cursor.execute(
        f"""
        SELECT r.request_id, r.day, r.request_start, r.transport, r.channel_name,
               r.client_phone, r.assigned_phone,
               COALESCE(u.name, r.c2d_operator_name) AS operator_name,
               r.incoming_messages, r.outgoing_messages, r.rating_score,
               {keys_mod.sql_key('r.client_phone')} AS client_key,
               {keys_mod.sql_key('r.assigned_phone')} AS assigned_key
          FROM c2d_requests r
          LEFT JOIN users u ON u.id = r.operator_id
         WHERE r.day BETWEEN %(start)s AND %(end)s
           AND COALESCE(r.request_type, 'common') = 'common'
           AND ({keys_mod.sql_key('r.client_phone')} = ANY(%(keys)s)
                OR {keys_mod.sql_key('r.assigned_phone')} = ANY(%(keys)s))
        """,
        {'keys': list(phone_keys), 'start': start, 'end': end},
    )
    wanted = set(phone_keys)
    items = []
    for row in _rows(cursor):
        key = row['client_key'] if row['client_key'] in wanted else row['assigned_key']
        at = row['request_start'] or datetime.combine(row['day'], datetime.min.time())
        messages = int(row['incoming_messages'] or 0) + int(row['outgoing_messages'] or 0)
        items.append(_item(
            'chat2desk', key, at,
            id=f"chat2desk:{row['request_id']}",
            direction='in',
            operator=row['operator_name'],
            messages=messages or None,
            note=row['channel_name'] or row['transport'],
        ))
    return items


def _chatapp(cursor, phone_keys, start, end):
    phone_expr = "COALESCE(NULLIF(c.phone, ''), c.chat_id)"
    cursor.execute(
        f"""
        WITH chats AS (
            SELECT c.license_id, c.messenger_type, c.chat_id, {keys_mod.sql_key(phone_expr)} AS phone_key
              FROM chatapp_chats c
             WHERE {keys_mod.sql_key(phone_expr)} = ANY(%(keys)s)
        )
        SELECT ch.license_id, ch.messenger_type, ch.chat_id, ch.phone_key,
               (m.dt AT TIME ZONE 'Asia/Almaty')::date AS day,
               MIN(m.dt) AS first_at,
               COUNT(*) AS messages,
               COUNT(*) FILTER (WHERE COALESCE(m.side, '') <> 'out') AS inbound,
               string_agg(DISTINCT COALESCE(u.name, om.employee_name), ', ')
                   FILTER (WHERE m.side = 'out' AND COALESCE(u.name, om.employee_name) IS NOT NULL) AS operators
          FROM chats ch
          JOIN chatapp_messages m
            ON m.license_id = ch.license_id AND m.messenger_type = ch.messenger_type AND m.chat_id = ch.chat_id
          LEFT JOIN chatapp_operator_map om ON om.employee_id = m.employee_id
          LEFT JOIN users u ON u.id = om.user_id
         WHERE m.dt >= %(ts_from)s AND m.dt < %(ts_to)s
           AND NOT m.is_deleted
         GROUP BY ch.license_id, ch.messenger_type, ch.chat_id, ch.phone_key, 5
        """,
        {'keys': list(phone_keys),
         'ts_from': datetime.combine(start, datetime.min.time(), ALMATY),
         'ts_to': datetime.combine(end + timedelta(days=1), datetime.min.time(), ALMATY)},
    )
    items = []
    for row in _rows(cursor):
        items.append(_item(
            'chatapp', row['phone_key'], row['first_at'],
            id=f"chatapp:{row['license_id']}:{row['chat_id']}:{row['day'].isoformat()}",
            direction='in' if row['inbound'] else 'out',
            operator=row['operators'],
            messages=int(row['messages'] or 0),
        ))
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Oktell — живой запрос, один на период, с коротким кешем
# ─────────────────────────────────────────────────────────────────────────────

_OKTELL_CACHE_SECONDS = 120.0
_oktell_cache_lock = threading.Lock()
_oktell_cache = {}


def oktell_sql(phone_keys, start, end):
    """Звонки СЗоВ с номерами реестра за период ОДНИМ запросом (прокси не любит
    серий). Номер клиента у входящих и исходящих — `[number]`, одиннадцать цифр
    (сверено на сутках 08.10.2026: пустых нет)."""
    condition = keys_mod.tsql_not_test('t.[number]', phone_keys)
    if not condition:
        return None
    # Условие «НЕ тестовый» переворачиваем: здесь нужны как раз тестовые.
    match = condition.replace(' NOT IN (', ' IN (', 1)
    date_from = start.strftime('%Y%m%d')
    date_to = (end + timedelta(days=1)).strftime('%Y%m%d')
    return (
        f"SELECT TOP {OKTELL_ROW_CAP} t.Id AS call_id, "
        "CONVERT(varchar(19), t.dt_insert, 120) AS occurred_at, t.route AS route, "
        "COALESCE(t.[number], N'') AS phone, t.taxi_park AS taxi_park, "
        "t.result_call AS result_call, t.call_result AS call_result, "
        "t.total_length AS total_length, oi.Name AS operator_name "
        "FROM oktell.dbo.Call_Systems_hst t "
        "LEFT JOIN oktell_cc_temp.dbo.A_Cube_CC_Cat_OperatorInfo oi "
        "ON oi.Id = TRY_CAST(t.id_operator AS uniqueidentifier) "
        f"WHERE t.dt_insert >= '{date_from}' AND t.dt_insert < '{date_to}' "
        f"AND {match} "
        "ORDER BY t.dt_insert"
    )


def _oktell(oktell_query, phone_keys, start, end, *, clock=time.monotonic):
    sql = oktell_sql(phone_keys, start, end)
    if not sql:
        return [], False
    cache_key = (tuple(sorted(phone_keys)), start, end)
    now = clock()
    with _oktell_cache_lock:
        cached = _oktell_cache.get(cache_key)
        if cached and now - cached[0] < _OKTELL_CACHE_SECONDS:
            return cached[1], cached[2]
    rows = oktell_query(sql)
    truncated = len(rows) >= OKTELL_ROW_CAP
    items = []
    for row in rows:
        route = str(row.get('route') or '')
        items.append(_item(
            'oktell', keys_mod.phone_key(row.get('phone')), row.get('occurred_at'),
            id=f"oktell:{row.get('call_id')}",
            direction='out' if route == 'outgoing' else 'in',
            operator=row.get('operator_name'),
            result=row.get('result_call') or None,
            duration_seconds=int(row.get('total_length') or 0),
            note=row.get('taxi_park') or None,
        ))
    with _oktell_cache_lock:
        _oktell_cache[cache_key] = (now, items, truncated)
        # Кеш маленький по природе (период × набор номеров), но не бесконечный.
        for stale in [k for k, v in _oktell_cache.items() if now - v[0] >= _OKTELL_CACHE_SECONDS]:
            _oktell_cache.pop(stale, None)
    return items, truncated


# ─────────────────────────────────────────────────────────────────────────────
# Сборка
# ─────────────────────────────────────────────────────────────────────────────

_DB_SOURCES = (
    ('freepbx', _freepbx),
    ('binotel', _binotel),
    ('wazzup', _wazzup),
    ('chat2desk', _chat2desk),
    ('chatapp', _chatapp),
)


def collect(get_cursor, phone_keys, start, end, *, oktell_query=None):
    """Все тесты периода: {'items': [...], 'days': [...], 'missing': [...]}.

    Каждый источник базы — своим курсором: упавший запрос откатывает только
    свою транзакцию, остальные источники дочитываются.
    """
    phone_keys = sorted({k for k in phone_keys if k})
    items, missing = [], []
    if phone_keys:
        for source, reader in _DB_SOURCES:
            try:
                with get_cursor() as cursor:
                    items.extend(reader(cursor, phone_keys, start, end))
            except Exception:  # noqa: BLE001 — один источник не роняет экран
                logging.exception('Реестр тестовых номеров: источник %s не прочитан', source)
                missing.append({'source': source, 'label': SOURCES[source]['label'],
                                'reason': 'не удалось прочитать'})
        if oktell_query is None:
            missing.append({'source': 'oktell', 'label': SOURCES['oktell']['label'],
                            'reason': 'Oktell не подключён'})
        else:
            try:
                oktell_items, truncated = _oktell(oktell_query, phone_keys, start, end)
                items.extend(oktell_items)
                if truncated:
                    missing.append({'source': 'oktell', 'label': SOURCES['oktell']['label'],
                                    'reason': f'показаны первые {OKTELL_ROW_CAP}'})
            except Exception:  # noqa: BLE001
                logging.exception('Реестр тестовых номеров: Oktell не ответил')
                missing.append({'source': 'oktell', 'label': SOURCES['oktell']['label'],
                                'reason': 'Oktell не ответил'})
    items = [item for item in items if item.get('day')]
    items.sort(key=lambda item: (item['at'] or '', item['id']), reverse=True)
    return {'items': items, 'days': per_day(items, start, end), 'missing': missing}


def per_day(items, start, end):
    """Тесты по дням периода, включая пустые дни — по убыванию даты."""
    counts = {}
    for item in items:
        bucket = counts.setdefault(item['day'], {'calls': 0, 'chats': 0})
        bucket['calls' if item['kind'] == 'call' else 'chats'] += 1
    days = []
    day = end
    while day >= start:
        key = day.isoformat()
        bucket = counts.get(key, {'calls': 0, 'chats': 0})
        days.append({'day': key, 'calls': bucket['calls'], 'chats': bucket['chats'],
                     'total': bucket['calls'] + bucket['chats']})
        day -= timedelta(days=1)
    return days
