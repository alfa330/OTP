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

У каждого теста — НАШ номер (`line`: на какой номер позвонили или написали, с
какого позвонили) и таксопарк (`park`; у Тез КЦ — название линии):

    freepbx    номер касания; парк — по очереди, у исходящего — по номеру (cdr/lines.py,
               то же правило, что в «Касаниях»)
    oktell     набранная линия цепочки (плечо «снаружи в IVR»); парк — taxi_park звонка
    binotel    линия из журнала Binotel (живой запрос по номерам реестра); названия нет
    wazzup     номер и название канала Wazzup
    chat2desk  номер канала Chat2Desk; парк — название канала
    chatapp    номер и название лицензии ChatApp

Справочники линий (API Wazzup, Chat2Desk, ChatApp, Binotel) приходят снаружи — от
бота, у которого есть ключи; без них тест показывается без номера линии.

Источник, который не ответил (Oktell лежит, таблицы нет на стенде), не роняет
экран: его тесты не показываются, а в ответе есть строка «чего не хватает».
"""

import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone

from . import keys as keys_mod
from . import rules as rules_mod

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
        'line': None,     # наш номер: «+7 747 577 77 78»
        'park': None,     # таксопарк (у Тез КЦ — название линии)
    }
    item.update(extra)
    return item


def _rows(cursor):
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def line_display(raw):
    """Наш номер на экран: «+7 747 577 77 78». Не номер (обрывок, имя) — None."""
    key = keys_mod.phone_key(raw)
    return rules_mod.display_phone(key) if key else None


def park_name(name, line=None):
    """Название канала без самого номера: «Центр регистрации 87475777778» → «Центр регистрации»
    (номер и так стоит рядом). Пустое — None."""
    text = ' '.join(str(name or '').split())
    key = keys_mod.phone_key(line) if line else None
    if key:
        text = ' '.join(token for token in text.split()
                        if not (token.isdigit() and keys_mod.phone_key(token) == key))
    return text or None


# ─────────────────────────────────────────────────────────────────────────────
# Наша база
# ─────────────────────────────────────────────────────────────────────────────

def _freepbx(cursor, phone_keys, start, end):
    # Номер касания — ровно десять цифр (cdr/touches.py: norm_phone), по нему индекс.
    # Сотрудник по внутреннему номеру — нынешний его владелец: номер уволившегося
    # отдают новому, а экрану тестов точная история номера не нужна.
    from cdr import lines as cdr_lines, queries as cdr_queries
    cursor.execute(
        """
        SELECT t.linkedid, t.phone, t.started_at, t.call_type, t.result, t.ext, t.queue,
               t.line_number,
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
    rows = _rows(cursor)
    # Парк — то же правило, что в «Касаниях» (cdr/lines.py): входящий — по своей
    # очереди, исходящий и бесочередной — по очереди номера. Справочник номеров
    # строится, только если такие касания есть (это проход по входящим за 60 дней).
    known = {}
    if any(row['line_number'] and (row['call_type'] == 'Исходящий' or not row['queue']) for row in rows):
        known = cdr_lines.line_queues(cdr_queries.line_queue_rows(
            cursor, cdr_queries.today_almaty() - timedelta(days=60)))
    items = []
    for row in rows:
        direction = 'out' if row['call_type'] == 'Исходящий' else 'in'
        items.append(_item(
            'freepbx', row['phone'], row['started_at'],
            id=f"freepbx:{row['linkedid']}:{row['phone']}",
            direction=direction,
            operator=row['operator_name'] or (f"вн. {row['ext']}" if row['ext'] else None),
            result=row['result'],
            duration_seconds=int(row['talk_seconds'] or 0),
            line=line_display(row['line_number']),
            park=cdr_lines.park(row, known) or None,
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
            # Линии зеркало не хранит: её отдаёт журнал Binotel (attach_lines).
            _line_ref={'call_id': str(row['general_call_id']), 'phone': row['phone_norm']},
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
            _line_ref={'account': row['account'], 'channel_id': str(row['channel_id'])},
        ))
    return items


def _chat2desk(cursor, phone_keys, start, end):
    # У клиента WhatsApp, пришедшего идентификатором, в client_phone лежит
    # «[wa_…] KZ.…», а номер — в assigned_phone: смотреть надо оба поля.
    cursor.execute(
        f"""
        SELECT r.request_id, r.day, r.request_start, r.transport, r.channel_id, r.channel_name,
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
    # Одна переписка за сутки — один тест, как у Wazzup и ChatApp: обращение, которое
    # оператор закрыл, а тестировщик вечером открыл заново, — та же переписка того же
    # дня в том же канале. Обращения склеиваются по (номер, канал, день).
    wanted = set(phone_keys)
    chats = {}
    for row in _rows(cursor):
        key = row['client_key'] if row['client_key'] in wanted else row['assigned_key']
        at = row['request_start'] or datetime.combine(row['day'], datetime.min.time())
        chat = chats.setdefault((key, row['channel_id'], row['day']),
                                {'at': at, 'messages': 0, 'operators': [],
                                 'park': row['channel_name'] or row['transport']})
        chat['at'] = min(chat['at'], at)
        chat['messages'] += int(row['incoming_messages'] or 0) + int(row['outgoing_messages'] or 0)
        if row['operator_name'] and row['operator_name'] not in chat['operators']:
            chat['operators'].append(row['operator_name'])
    # Канал Chat2Desk назван по парку («Jana Taxi», «Ноль такси»), номер канала —
    # из справочника каналов (attach_lines).
    return [_item('chat2desk', key, chat['at'],
                  id=f"chat2desk:{key}:{channel_id}:{day.isoformat()}",
                  direction='in',
                  operator=', '.join(chat['operators']) or None,
                  messages=chat['messages'] or None,
                  park=park_name(chat['park']),
                  _line_ref={'channel_id': channel_id})
            for (key, channel_id, day), chat in chats.items()]


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
            id=f"chatapp:{row['license_id']}:{row['messenger_type']}:{row['chat_id']}:{row['day'].isoformat()}",
            direction='in' if row['inbound'] else 'out',
            operator=row['operators'],
            messages=int(row['messages'] or 0),
            _line_ref={'license_id': row['license_id'], 'messenger_type': row['messenger_type']},
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
    (сверено на сутках 08.10.2026: пустых нет).

    Наш номер — набранная линия цепочки: `ANumberDialed` у плеча «снаружи в IVR»
    (ConnectionType = 4, есть у каждого входящего). Поиск по индексу IdChain: на
    живом прокси 0,2 с на 79 звонков. У исходящего такого плеча нет — линия пустая."""
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
        "t.total_length AS total_length, oi.Name AS operator_name, l.ANumberDialed AS line "
        "FROM oktell.dbo.Call_Systems_hst t "
        "LEFT JOIN oktell_cc_temp.dbo.A_Cube_CC_Cat_OperatorInfo oi "
        "ON oi.Id = TRY_CAST(t.id_operator AS uniqueidentifier) "
        "OUTER APPLY (SELECT TOP 1 s.ANumberDialed FROM oktell.dbo.A_Stat_Connections_1x1 s "
        "WHERE s.IdChain = TRY_CONVERT(uniqueidentifier, t.chainid) AND s.ConnectionType = 4 "
        "ORDER BY s.TimeStart) l "
        f"WHERE t.dt_insert >= '{date_from}' AND t.dt_insert < '{date_to}' "
        f"AND {match} "
        # Упрёмся в потолок — пусть пропадут старые, а не сегодняшние: их смотрят чаще.
        "ORDER BY t.dt_insert DESC"
    )


def _oktell(oktell_query, phone_keys, start, end, *, clock=time.monotonic, line_key=None, park_label=None):
    """line_key — ключ линии из биллинга Oktell (чинит записи вида «7639iTaxi»),
    park_label — подпись парка, которой его называет бизнес («Hokage» → «Wolt»).
    Без них — последние 10 цифр и taxi_park как есть."""
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
        park = str(row.get('taxi_park') or '').strip()
        line = line_key(row.get('line')) if line_key else row.get('line')
        items.append(_item(
            'oktell', keys_mod.phone_key(row.get('phone')), row.get('occurred_at'),
            id=f"oktell:{row.get('call_id')}",
            direction='out' if route == 'outgoing' else 'in',
            operator=row.get('operator_name'),
            result=row.get('result_call') or None,
            duration_seconds=int(row.get('total_length') or 0),
            line=line_display(line),
            park=(park_label(park) if park_label else park) if park else None,
        ))
    with _oktell_cache_lock:
        _oktell_cache[cache_key] = (now, items, truncated)
        # Кеш маленький по природе (период × набор номеров), но не бесконечный.
        for stale in [k for k, v in _oktell_cache.items() if now - v[0] >= _OKTELL_CACHE_SECONDS]:
            _oktell_cache.pop(stale, None)
    return items, truncated


# ─────────────────────────────────────────────────────────────────────────────
# Наш номер и парк из справочников API
# ─────────────────────────────────────────────────────────────────────────────

# Справочники линий приходят от бота (у него ключи API), каждый — по желанию:
#   oktell_line_key(raw) -> '7075050880' | ''     ключ линии биллинга Oktell
#   oktell_park_label(name) -> str                подпись парка Oktell
#   wazzup_channels(account) -> [{'channelId', 'name', 'plainId'}, ...]
#   c2d_channels() -> {id канала: номер}
#   chatapp_lines() -> {(licenseId, messengerType): (название, номер)}
#   binotel_lines(номера клиентов) -> {generalCallID: номер линии}
LINE_LOOKUPS = ('oktell_line_key', 'oktell_park_label', 'wazzup_channels', 'c2d_channels',
                'chatapp_lines', 'binotel_lines')

# Журнал Binotel по номерам отдаёт всю их историю — один запрос на набор номеров,
# повтор экрана в пределах кеша его не повторяет.
_BINOTEL_LINES_CACHE_SECONDS = 120
_binotel_cache = {}
_binotel_cache_lock = threading.Lock()


def _binotel_lines(fetch, phones, *, clock):
    cache_key = tuple(phones)
    now = clock()
    with _binotel_cache_lock:
        cached = _binotel_cache.get(cache_key)
        if cached and now - cached[0] < _BINOTEL_LINES_CACHE_SECONDS:
            return cached[1]
    found = {str(call_id): line for call_id, line in (fetch(list(phones)) or {}).items()}
    with _binotel_cache_lock:
        _binotel_cache[cache_key] = (now, found)
        for stale in [k for k, v in _binotel_cache.items() if now - v[0] >= _BINOTEL_LINES_CACHE_SECONDS]:
            _binotel_cache.pop(stale, None)
    return found


def attach_lines(items, lines, *, clock=time.monotonic):
    """Наш номер и название линии туда, где их знает только API (Binotel, Wazzup,
    Chat2Desk, ChatApp). Справочник не ответил — тест остаётся, без номера; вернётся
    список таких источников, чтобы экран сказал, чего не хватает."""
    lines = lines or {}
    pending = {}
    for item in items:
        ref = item.pop('_line_ref', None)
        if ref is not None:
            pending.setdefault(item['source'], []).append((item, ref))
    failed = []

    def wazzup(found):
        channels = {}
        for account in sorted({ref['account'] for _, ref in found}):
            for channel in lines['wazzup_channels'](account) or []:
                channels[(account, str(channel.get('channelId')))] = channel
        for item, ref in found:
            channel = channels.get((ref['account'], ref['channel_id']))
            if channel:
                item['line'] = line_display(channel.get('plainId'))
                item['park'] = park_name(channel.get('name'), channel.get('plainId'))

    def chat2desk(found):
        phones = {int(k): v for k, v in (lines['c2d_channels']() or {}).items()}
        for item, ref in found:
            phone = phones.get(int(ref['channel_id'])) if ref['channel_id'] is not None else None
            if phone:
                item['line'] = line_display(phone)
                item['park'] = park_name(item['park'], phone)

    def chatapp(found):
        licenses = {(int(k[0]), str(k[1])): v for k, v in (lines['chatapp_lines']() or {}).items()}
        for item, ref in found:
            name, phone = licenses.get((int(ref['license_id']), str(ref['messenger_type'])), (None, None))
            item['line'] = line_display(phone)
            item['park'] = park_name(name, phone)

    def binotel(found):
        phones = sorted({ref['phone'] for _, ref in found if ref['phone']})
        by_call = _binotel_lines(lines['binotel_lines'], phones, clock=clock)
        for item, ref in found:
            item['line'] = line_display(by_call.get(ref['call_id']))

    for source, lookup, fill in (('wazzup', 'wazzup_channels', wazzup), ('chat2desk', 'c2d_channels', chat2desk),
                                 ('chatapp', 'chatapp_lines', chatapp), ('binotel', 'binotel_lines', binotel)):
        if not pending.get(source) or not lines.get(lookup):
            continue
        try:
            fill(pending[source])
        except Exception:  # noqa: BLE001 — без номера линии тест всё равно показывается
            logging.exception('Реестр тестовых номеров: номера линий %s не получены', source)
            failed.append(source)
    return failed


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


def collect(get_cursor, phone_keys, start, end, *, oktell_query=None, lines=None):
    """Все тесты периода: {'items': [...], 'days': [...], 'missing': [...]}.

    Каждый источник базы — своим курсором: упавший запрос откатывает только
    свою транзакцию, остальные источники дочитываются. lines — справочники линий
    (LINE_LOOKUPS): наш номер и парк там, где их знает только API.
    """
    lines = lines or {}
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
                oktell_items, truncated = _oktell(oktell_query, phone_keys, start, end,
                                                  line_key=lines.get('oktell_line_key'),
                                                  park_label=lines.get('oktell_park_label'))
                items.extend(oktell_items)
                if truncated:
                    missing.append({'source': 'oktell', 'label': SOURCES['oktell']['label'],
                                    'reason': f'показаны последние {OKTELL_ROW_CAP}'})
            except Exception:  # noqa: BLE001
                logging.exception('Реестр тестовых номеров: Oktell не ответил')
                missing.append({'source': 'oktell', 'label': SOURCES['oktell']['label'],
                                'reason': 'Oktell не ответил'})
    for source in attach_lines(items, lines):
        missing.append({'source': source, 'label': SOURCES[source]['label'],
                        'reason': 'номера линий не получены'})
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
