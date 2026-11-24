"""Биллинг чата по Syntony («Расчёт ресурсов → Чат → Биллинг»), с 18.11.2026.

Обращений, как у Chat2Desk, у Syntony нет: диалог сам не закрывается, водитель пишет в него
несколько дней. Решение владельца 24.11.2026 — считать «как на табло»:

* **Поступило** — диалоги СЗоВ, где водитель писал в эти сутки («всего чатов» табло);
* **Обслужено** — где ему ответил сотрудник; **без ответа** — остальные, и чаты, которые вёл
  только ИИ;
* **первый ответ и SL** — по диалогам, начатым в эти сутки: от первого сообщения водителя, а
  если чат передал ИИ — от передачи (правило табло, metrics.conversation_facts);
* **ответ внутри чата** — среднее по ходам водителя, начатым в эти сутки (тоже правило табло);
* **сотрудники по часам** — секунды статуса «Онлайн» Syntony; за сутки без статусов —
  онлайн по ответам за 15 минут, как считало табло до статусов.

Переписка Syntony хранится 30 дней (sync.KEEP_DAYS), а биллинг смотрят за любой месяц, поэтому
сутки сворачиваются в «чат дня» — строку на диалог, как обращение Chat2Desk в c2d_requests, —
и в разрез «диалог × сотрудник» для вкладки «Чатники». Сегодняшние сутки и вчерашние до
полудня пересобираются каждым прогоном (ответ на вечерний чат приходит ночью), прошлые — пока
не собраны целиком. Читает это resource_fte/chat.py рядом с c2d_requests.
"""

import logging
from datetime import date, datetime, timedelta, timezone

from psycopg2.extras import execute_values

from . import metrics, statuses
from . import szov

logger = logging.getLogger(__name__)

# Названия парков — как у Chat2Desk в c2d_requests: биллинг за период через 18.11 и доли плана
# по паркам (CHAT_BILLING_PARK_SHARES) должны видеть один парк под одним именем. Ключ — канал
# Syntony (szov.SZOV_CHANNEL_IDS); номер канала с номером Chat2Desk сверен 20.11.2026.
PARK_BY_CHANNEL = {
    '22970e91-2056-4611-9bbf-5a3f24b7abee': 'iPartner',
    'ae11b9f5-cada-49b5-b7bc-c3c01ae695f0': 'Salam taxi',
    'b19365ae-210f-4a87-bdfd-ef0397639b75': 'Техподдержка iTaxi',
    '631f59ac-6815-4164-955d-a27e7613fae3': 'Jana Taxi',
    'b40df588-75cc-4562-be29-b1e73580fd49': 'Ноль такси',
    '39114c57-557e-4549-9af2-7d169524272e': 'Ноль такси 2',
    '9aaf8a0d-aa84-4e34-afb2-6e1af2d47ba1': 'Такси24(Нуртакси)',
    '95c5f818-021a-45fa-a003-34d455916f07': 'Аманат',
    'cae48e5d-ad73-4727-ad5f-ccfd33833a54': 'QAZAQ',
    '5be651eb-c630-45af-b0dc-388ccd72b0cd': 'Tenge Taxi',
    '3660595b-557f-4c42-bada-61713ec5251c': 'Бизнес Партнер',
    'bc40028b-b5df-411c-93ed-16d9032b9d56': 'Достойный',
    '0b25d563-3c4a-4c49-93fb-1f3e4418416b': 'Стабильный',
    'c5e58281-1991-4081-9a10-42c0fac6dc72': 'Халык',
    '4332d1e6-d9dd-4053-9daf-8e05f5d41153': 'Честный',
    '4806fda0-458e-44b1-a79e-1682d921d409': 'Центр регистрации',
    '6f96c35f-08b2-4f47-ad72-607d65bb1b57': 'Центр регистрации',
}

# Ответ на чат, начатый в 23:50, приходит уже ночью: сутки читаются с запасом 12 часов после
# конца (как выгрузка табло, szov.day_block) и считаются собранными целиком, когда запас прошёл.
LOOKAHEAD = timedelta(hours=12)
# Переписка старше 30 дней удаляется — такие сутки уже не пересобрать.
REBUILD_MAX_AGE_DAYS = 29
# Догонять прошлые сутки — не больше стольких за прогон: каждые сутки — чтение ~25 тыс. сообщений.
BACKFILL_DAYS_PER_RUN = 3

STAFF_FROM_STATUSES = 'status'
STAFF_FROM_REPLIES = 'replies'


def park_of(channel_id, park_name):
    return PARK_BY_CHANNEL.get(channel_id) or park_name or None


def transport_of(channel_type, channel_provider):
    """Канал связи для разреза «Транспорт»: WABA — официальный API, иначе тип канала."""
    provider = str(channel_provider or '').lower()
    if provider.startswith('waba'):
        return 'WABA'
    kind = str(channel_type or '').strip()
    return {'whatsapp': 'WhatsApp', 'telegram': 'Telegram'}.get(kind.lower(), kind or None)


def _wall(moment):
    """Время для колонок TIMESTAMP — стенными часами Алматы, как request_start у Chat2Desk."""
    return moment.astimezone(szov.LOCAL_TZ).replace(tzinfo=None)


def _mean(values):
    return (sum(values) / len(values)) if values else None


def _seconds(later, earlier):
    return max(0.0, (later - earlier).total_seconds())


def day_rows(facts, details, ctx, start, end):
    """(чаты дня, разрез по сотрудникам) суток [start, end) из фактов диалогов СЗоВ.

    facts — metrics.analyze; details — {id диалога: {channel_id, channel_name, channel_type,
    channel_provider, park_name, contact_phone, contact_name}}.
    """
    chats, people = [], []
    for conversation_id, item in facts.items():
        detail = details.get(conversation_id) or {}
        park = park_of(item.conversation.channel_id or detail.get('channel_id'), detail.get('park_name'))
        day_driver = [moment for moment in item.driver_times if start <= moment < end]
        started = day_driver[0] if day_driver else None
        created = item.conversation.created_at
        is_new = bool(day_driver) and created is not None and start <= created < end
        day_turns = [(begun, reply_at, author) for begun, reply_at, author in item.turns
                     if start <= begun < end]
        day_replies = [(moment, author) for moment, author in item.replies if start <= moment < end]
        if started is not None:
            after = sorted((moment, author) for moment, author in item.replies if moment >= started)
            first_author = after[0][1] if after else None
            operator = ctx.resolve(first_author) if first_author else None
            chats.append({
                'conversation_id': conversation_id,
                'park': park,
                'channel_id': item.conversation.channel_id,
                'channel_name': detail.get('channel_name'),
                'transport': transport_of(detail.get('channel_type'), detail.get('channel_provider')),
                'client_phone': detail.get('contact_phone'),
                'client_name': detail.get('contact_name'),
                'started_at': _wall(started),
                'is_new': is_new,
                # Ответил сотрудник: на ход, начатый в эти сутки (хоть и после полуночи), или
                # сообщением в эти сутки после первого сообщения водителя.
                'answered': bool(day_turns) or any(moment >= started for moment, _ in day_replies),
                'first_reply_seconds': item.first_reaction_seconds if is_new else None,
                'inner_reply_seconds': _mean([_seconds(reply_at, begun)
                                              for begun, reply_at, _a in day_turns]),
                'operator_id': int(operator['id']) if operator else None,
                'operator_name': ctx.display_name(first_author) if first_author else None,
                'incoming_messages': len(day_driver),
                'outgoing_messages': len(day_replies),
            })
        # Разрез по сотрудникам — правило metrics.operator_sums: чат засчитан каждому, кто
        # отвечал в эти сутки; первый ответ — ответившему первым в начатом сегодня диалоге;
        # ответ внутри чата — его ответы на ходы, начатые сегодня.
        per_key = {}

        def bucket(author):
            key = ctx.display_name(author) if author else None
            if not key:
                return None
            if key not in per_key:
                operator = ctx.resolve(author)
                per_key[key] = {'operator_key': key, 'operator_name': key,
                                'operator_id': int(operator['id']) if operator else None,
                                'replied': False, 'first': None, 'waits': [], 'out': 0,
                                'first_reply_at': None}
            return per_key[key]

        for moment, author in day_replies:
            entry = bucket(author)
            if entry is None:
                continue
            entry['replied'] = True
            entry['out'] += 1
            if entry['first_reply_at'] is None or moment < entry['first_reply_at']:
                entry['first_reply_at'] = moment
        if (item.first_reaction_seconds is not None and item.first_reaction_author
                and created is not None and start <= created < end):
            entry = bucket(item.first_reaction_author)
            if entry is not None:
                entry['first'] = item.first_reaction_seconds
        for begun, reply_at, author in day_turns:
            entry = bucket(author)
            if entry is not None:
                entry['waits'].append(_seconds(reply_at, begun))
        for entry in per_key.values():
            # Окно времени суток в биллинге режет по началу чата: у сотрудника это первое
            # сообщение водителя за сутки, а если водитель сегодня не писал — его ответ.
            moment = started or entry['first_reply_at'] or start
            people.append({
                'conversation_id': conversation_id,
                'operator_key': entry['operator_key'][:200],
                'operator_id': entry['operator_id'],
                'operator_name': entry['operator_name'],
                'park': park,
                'started_at': _wall(moment),
                'replied': entry['replied'],
                'first_reply_seconds': entry['first'],
                'inner_reply_seconds': _mean(entry['waits']),
                'incoming_messages': len(day_driver),
                'outgoing_messages': entry['out'],
            })
    return chats, people


def reply_line_seconds_by_hour(activity, resolve, day_start, hours, until=None,
                               window_seconds=metrics.PRESENCE_WINDOW_SECONDS):
    """{час: {id сотрудника: секунды онлайн}} по ответам — правило табло до статусов."""
    moments = {}
    for author, items in (activity or {}).items():
        operator = resolve(author)
        if operator is not None:
            moments.setdefault(int(operator['id']), []).extend(items)
    per_hour = {}
    for operator_id, items in moments.items():
        for begin, end in metrics.online_intervals(items, window_seconds, until=until):
            for hour in range(hours):
                hour_start = day_start + timedelta(hours=hour)
                overlap = (min(end, hour_start + timedelta(hours=1)) - max(begin, hour_start)).total_seconds()
                if overlap > 0:
                    people = per_hour.setdefault(hour, {})
                    people[operator_id] = people.get(operator_id, 0) + int(overlap)
    return per_hour


def staff_rows(per_hour, source):
    return [{'hour': hour, 'operator_id': operator_id, 'online_seconds': int(seconds),
             'source': source}
            for hour, people in sorted(per_hour.items())
            for operator_id, seconds in sorted(people.items()) if seconds > 0]


# ── База ──────────────────────────────────────────────────────────────────────────────────

_CHAT_COLUMNS = ('conversation_id', 'park', 'channel_id', 'channel_name', 'transport',
                 'client_phone', 'client_name', 'started_at', 'is_new', 'answered',
                 'first_reply_seconds', 'inner_reply_seconds', 'operator_id', 'operator_name',
                 'incoming_messages', 'outgoing_messages')
_PEOPLE_COLUMNS = ('conversation_id', 'operator_key', 'operator_id', 'operator_name', 'park',
                   'started_at', 'replied', 'first_reply_seconds', 'inner_reply_seconds',
                   'incoming_messages', 'outgoing_messages')


def _details(cursor, conversation_ids):
    if not conversation_ids:
        return {}
    cursor.execute("""
        SELECT id, channel_id, channel_name, channel_type, channel_provider, park_name,
               contact_phone, contact_name
          FROM syntony_chat_conversations WHERE id = ANY(%s)
    """, (sorted(conversation_ids),))
    keys = ('channel_id', 'channel_name', 'channel_type', 'channel_provider', 'park_name',
            'contact_phone', 'contact_name')
    return {row[0]: dict(zip(keys, row[1:])) for row in cursor.fetchall()}


def save_day(cursor, day, chats, people, staff, *, complete, staff_source, error=None):
    """Сутки целиком одной транзакцией. Строка чата — upsert: её id (снимок «Детализации»)
    не меняется от пересборки; ушедшие из суток строки удаляются."""
    if chats:
        execute_values(cursor, """
            INSERT INTO syntony_chat_billing_chats (day, {columns}) VALUES %s
            ON CONFLICT (day, conversation_id) DO UPDATE SET {updates}, built_at = now()
        """.format(columns=', '.join(_CHAT_COLUMNS),
                   updates=', '.join('{0} = EXCLUDED.{0}'.format(name)
                                     for name in _CHAT_COLUMNS if name != 'conversation_id')),
            [(day,) + tuple(row[name] for name in _CHAT_COLUMNS) for row in chats], page_size=500)
    cursor.execute("""
        DELETE FROM syntony_chat_billing_chats
         WHERE day = %s AND NOT (conversation_id = ANY(%s))
    """, (day, [row['conversation_id'] for row in chats]))
    cursor.execute('DELETE FROM syntony_chat_billing_operators WHERE day = %s', (day,))
    if people:
        execute_values(cursor, """
            INSERT INTO syntony_chat_billing_operators (day, conversation_id, operator_key,
                operator_id, operator_name, park, started_at, replied, first_reply_seconds,
                inner_reply_seconds, incoming_messages, outgoing_messages)
            VALUES %s
            ON CONFLICT (day, conversation_id, operator_key) DO NOTHING
        """, [(day, row['conversation_id'], row['operator_key'], row['operator_id'],
               row['operator_name'], row['park'], row['started_at'], row['replied'],
               row['first_reply_seconds'], row['inner_reply_seconds'], row['incoming_messages'],
               row['outgoing_messages'])
              for row in people if row['replied'] or row['first_reply_seconds'] is not None
              or row['inner_reply_seconds'] is not None], page_size=500)
    cursor.execute('DELETE FROM syntony_chat_billing_staff WHERE day = %s', (day,))
    if staff:
        execute_values(cursor, """
            INSERT INTO syntony_chat_billing_staff (day, hour, operator_id, online_seconds, source)
            VALUES %s
        """, [(day, row['hour'], row['operator_id'], row['online_seconds'], row['source'])
              for row in staff], page_size=500)
    cursor.execute("""
        INSERT INTO syntony_chat_billing_days (day, built_at, complete, chats, staff_source, error)
        VALUES (%s, now(), %s, %s, %s, %s)
        ON CONFLICT (day) DO UPDATE SET built_at = now(), complete = EXCLUDED.complete,
            chats = EXCLUDED.chats, staff_source = EXCLUDED.staff_source, error = EXCLUDED.error
    """, (day, bool(complete), len(chats), staff_source, error))


def _note_error(db, day, error):
    with db._get_cursor() as cursor:
        cursor.execute("""
            INSERT INTO syntony_chat_billing_days (day, built_at, complete, chats, error)
            VALUES (%s, now(), FALSE, 0, %s)
            ON CONFLICT (day) DO UPDATE SET built_at = now(), error = EXCLUDED.error
        """, (day, error[:300]))


def build_day(db, ctx, day, *, now):
    """Собрать и записать сутки → {'day', 'chats', 'complete', 'staff_source'} или {'error'}."""
    start, end = szov.local_day_bounds(day)
    load_end = min(end + LOOKAHEAD, now + timedelta(seconds=1))
    facts, activity, state, _messages = szov._load(db, ctx, start - szov.LOOKBACK, load_end)
    error = szov._covered_error(state, start)
    if error:
        # Переписки за эти сутки в базе нет (данные начались позже) — нулей не пишем: в биллинге
        # они читались бы как простой смены.
        _note_error(db, day, error)
        return {'day': day.isoformat(), 'error': error}
    with db._get_cursor() as cursor:
        details = _details(cursor, facts.keys())
    chats, people = day_rows(facts, details, ctx, start, end)
    until = min(end, now)
    hours = 24 if now >= end else min(24, int((now - start).total_seconds() // 3600) + 1)
    status_state, intervals, _seen = szov._load_statuses(db, start, until)
    if szov.statuses_cover(status_state, start, until - timedelta(minutes=5) if now < end else end):
        match = statuses.matcher(ctx.roster, ctx.resolve)
        staff = staff_rows(statuses.line_seconds_by_hour(intervals, match, start, hours, until=until),
                           STAFF_FROM_STATUSES)
        source = STAFF_FROM_STATUSES
    else:
        staff = staff_rows(reply_line_seconds_by_hour(activity, ctx.resolve, start, hours,
                                                      until=until, window_seconds=ctx.window_seconds),
                           STAFF_FROM_REPLIES)
        source = STAFF_FROM_REPLIES
    complete = now >= end + LOOKAHEAD
    with db._get_cursor() as cursor:
        save_day(cursor, day, chats, people, staff, complete=complete, staff_source=source)
    return {'day': day.isoformat(), 'chats': len(chats), 'complete': complete, 'staff_source': source}


def days_to_build(journal, *, first_day, now):
    """Какие сутки собирать сейчас: сегодня всегда; вчера и раньше — пока не собраны целиком
    (не дальше 29 дней: переписка старше удалена), прошлые — не больше 3 за прогон, свежие сначала."""
    today = now.astimezone(szov.LOCAL_TZ).date()
    floor = max(first_day, today - timedelta(days=REBUILD_MAX_AGE_DAYS))
    pending = []
    day = today - timedelta(days=1)
    while day >= floor:
        if not (journal.get(day) or {}).get('complete'):
            pending.append(day)
        day -= timedelta(days=1)
    return ([today] if today >= first_day else []) + pending[:BACKFILL_DAYS_PER_RUN]


def refresh(db, ctx, *, first_day, now=None):
    """Прогон: собрать нужные сутки → [итог по суткам]. Ошибка одних суток другие не держит."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with db._get_cursor() as cursor:
        cursor.execute('SELECT day, complete, error FROM syntony_chat_billing_days WHERE day >= %s',
                       (first_day,))
        journal = {row[0]: {'complete': row[1], 'error': row[2]} for row in cursor.fetchall()}
    results = []
    for day in days_to_build(journal, first_day=first_day, now=now):
        try:
            results.append(build_day(db, ctx, day, now=now))
        except Exception as exc:  # noqa: BLE001
            logger.exception('Биллинг чата по Syntony: сутки %s не собрались', day)
            results.append({'day': day.isoformat(), 'error': type(exc).__name__})
    return results


def parse_first_day(value, default):
    try:
        return date.fromisoformat(str(value).strip()) if value else default
    except ValueError:
        return default
