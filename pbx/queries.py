# -*- coding: utf-8 -*-
"""SQL-слой раздела «АТС». Функции принимают готовый курсор (Database._get_cursor) и не
управляют транзакцией — её держит вызывающий, как в cdr/queries.py.

Две ловушки, учтённые здесь
---------------------------
* Пересылка живого состояния идёт пачками и после обрыва может прийти второй раз в старом
  виде. Поэтому законченный звонок никогда не «оживает»: запись с state='ended' обновляется
  только записью с тем же состоянием. Порядковый номер коннектора (seq) для этого не годится
  — после его перезапуска нумерация начинается заново.
* `pbx_connector_state` — TIMESTAMPTZ, сессия Postgres на Render живёт в UTC: наружу
  уходит ISO с поясом, фронт переводит сам. Время звонков — TIMESTAMP без пояса (местное
  станции), как у касаний: отдаётся строкой «YYYY-MM-DDTHH:MM:SS».
"""

import json
from datetime import datetime, timedelta

from psycopg2.extras import Json, execute_values

from cdr import queries as cdr_queries, pbx_calls, touches as touches_mod

from .schema import COMMAND_TTL_SECONDS, PBX_CALLS_RETENTION_DAYS, PBX_COMMANDS_RETENTION_DAYS

now_almaty = cdr_queries.now_almaty
load_access_context = cdr_queries.load_access_context

CALL_FIELDS = ('linkedid', 'direction', 'phone', 'did', 'queue', 'ext', 'ringing', 'state',
               'started_at', 'queued_at', 'ringing_at', 'answered_at', 'ended_at', 'wait_seconds',
               'hangup_cause', 'hangup_side', 'dial_prefix', 'dial_status', 'channel',
               'agent_channel', 'recording_file', 'origin', 'command_id', 'seq')

_TIME_FIELDS = ('started_at', 'queued_at', 'ringing_at', 'answered_at', 'ended_at')


def _ts(value):
    """«YYYY-MM-DDTHH:MM:SS» → datetime или None. Мусор — None: лучше пустая ячейка, чем
    упавшая пачка."""
    text = str(value or '')[:19]
    if len(text) != 19:
        return None
    try:
        return datetime.strptime(text, '%Y-%m-%dT%H:%M:%S')
    except ValueError:
        return None


def _stamp(value):
    if value is None:
        return ''
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%dT%H:%M:%S')
    return str(value)[:19].replace(' ', 'T')


def clean_call(item):
    """Звонок из тела запроса коннектора → то, что сохраним; мусор → None.

    Тело приходит снаружи: всё режется и нормализуется здесь, а не в SQL."""
    if not isinstance(item, dict):
        return None
    linkedid = str(item.get('linkedid') or '')[:64].strip()
    started = _ts(item.get('started_at'))
    if not linkedid or started is None:
        return None
    state = str(item.get('state') or 'new')
    if state not in pbx_calls.STATES:
        return None
    direction = str(item.get('direction') or '')[:12]
    if direction not in ('', pbx_calls.DIRECTION_IN, pbx_calls.DIRECTION_OUT,
                         pbx_calls.DIRECTION_INTERNAL, pbx_calls.DIRECTION_AUTODIAL):
        direction = ''
    ringing = item.get('ringing') or []
    if not isinstance(ringing, list):
        ringing = []
    ringing = sorted({str(x)[:8] for x in ringing if str(x).strip()})[:20]
    side = str(item.get('hangup_side') or '')
    try:
        command_id = int(item['command_id']) if item.get('command_id') is not None else None
    except (TypeError, ValueError):
        command_id = None
    return {
        'linkedid': linkedid,
        'direction': direction,
        'phone': touches_mod.norm_phone(item.get('phone')),
        'did': str(item.get('did') or '')[:24],
        'queue': str(item.get('queue') or '')[:8],
        'ext': str(item.get('ext') or '')[:8],
        'ringing': ringing,
        'state': state,
        'started_at': started,
        'queued_at': _ts(item.get('queued_at')),
        'ringing_at': _ts(item.get('ringing_at')),
        'answered_at': _ts(item.get('answered_at')),
        'ended_at': _ts(item.get('ended_at')),
        'wait_seconds': _int_or_none(item.get('wait_seconds')),
        'hangup_cause': _int_or_none(item.get('hangup_cause')),
        'hangup_side': side if side in ('operator', 'client') else '',
        'dial_prefix': str(item.get('dial_prefix') or '')[:8],
        'dial_status': str(item.get('dial_status') or '')[:16],
        'channel': str(item.get('channel') or '')[:120],
        'agent_channel': str(item.get('agent_channel') or '')[:120],
        'recording_file': str(item.get('recording_file') or '')[:200],
        'origin': 'portal' if item.get('origin') == 'portal' else '',
        'command_id': command_id,
        'seq': _int_or_none(item.get('seq')) or 0,
    }


def _int_or_none(value):
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── коннектор ────────────────────────────────────────────────────────────────

def connector_seen(cursor, *, hostname=None, version=None, agent_key=None, ami=None,
                   snapshot=False):
    ami = ami or {}
    cursor.execute("""
        INSERT INTO pbx_connector_state (id, last_seen_at, hostname, version, agent_key,
                                         ami_connected, ami_host, ami_error, events_total,
                                         snapshot_at)
        VALUES (1, NOW(), %(host)s, %(version)s, %(key)s, %(ami_ok)s, %(ami_host)s, %(ami_error)s,
                %(events)s, CASE WHEN %(snapshot)s THEN NOW() ELSE NULL END)
        ON CONFLICT (id) DO UPDATE SET
            last_seen_at  = NOW(),
            hostname      = COALESCE(EXCLUDED.hostname, pbx_connector_state.hostname),
            version       = COALESCE(EXCLUDED.version, pbx_connector_state.version),
            agent_key     = COALESCE(EXCLUDED.agent_key, pbx_connector_state.agent_key),
            ami_connected = EXCLUDED.ami_connected,
            ami_host      = COALESCE(EXCLUDED.ami_host, pbx_connector_state.ami_host),
            ami_error     = EXCLUDED.ami_error,
            events_total  = GREATEST(EXCLUDED.events_total, pbx_connector_state.events_total),
            snapshot_at   = CASE WHEN %(snapshot)s THEN NOW() ELSE pbx_connector_state.snapshot_at END
    """, {'host': (str(hostname)[:120] if hostname else None),
          'version': (str(version)[:40] if version else None),
          'key': (str(agent_key)[:32] if agent_key else None),
          'ami_ok': bool(ami.get('connected')),
          'ami_host': (str(ami.get('host'))[:120] if ami.get('host') else None),
          'ami_error': (str(ami.get('error'))[:500] if ami.get('error') else None),
          'events': int(ami.get('events_total') or 0),
          'snapshot': bool(snapshot)})


def save_lines(cursor, lines):
    cursor.execute("""
        INSERT INTO pbx_connector_state (id, lines, lines_at) VALUES (1, %s, NOW())
        ON CONFLICT (id) DO UPDATE SET lines = EXCLUDED.lines, lines_at = NOW()
    """, (Json(lines),))


def connector_state(cursor):
    cursor.execute("""
        SELECT last_seen_at, hostname, version, agent_key, ami_connected, ami_host, ami_error,
               events_total, snapshot_at, lines, lines_at
          FROM pbx_connector_state WHERE id = 1
    """)
    row = cursor.fetchone()
    if not row:
        return {'last_seen_at': None, 'ami_connected': False, 'lines': {}}
    lines = row[9] if isinstance(row[9], dict) else json.loads(row[9] or '{}')
    return {
        'last_seen_at': row[0].isoformat() if row[0] else None,
        'hostname': row[1], 'version': row[2], 'agent_key': row[3],
        'ami_connected': bool(row[4]), 'ami_host': row[5], 'ami_error': row[6],
        'events_total': row[7],
        'snapshot_at': row[8].isoformat() if row[8] else None,
        'lines': lines or {},
        'lines_at': row[10].isoformat() if row[10] else None,
    }


# ── звонки ───────────────────────────────────────────────────────────────────

def upsert_calls(cursor, calls):
    """Пачка состояний от коннектора. Законченный звонок не оживает: строка с state='ended'
    принимает только state='ended' (у второго прихода старой пачки после обрыва)."""
    if not calls:
        return 0
    rows = [tuple(Json(call['ringing']) if field == 'ringing' else call[field] for field in CALL_FIELDS)
            for call in calls]
    columns = ', '.join(CALL_FIELDS)
    updates = ', '.join('%s = EXCLUDED.%s' % (f, f) for f in CALL_FIELDS if f != 'linkedid')
    execute_values(cursor, """
        INSERT INTO pbx_calls (%s) VALUES %%s
        ON CONFLICT (linkedid) DO UPDATE SET %s, updated_at = NOW()
        WHERE pbx_calls.state <> 'ended' OR EXCLUDED.state = 'ended'
    """ % (columns, updates), rows)
    return len(rows)


def close_missing(cursor, known_linkedids, now=None):
    """После полного снимка: незаконченные у нас звонки, которых в снимке нет, закрываются —
    событие отбоя потерялось. Только свежие (сутки): старее — уже уборка."""
    moment = (now or now_almaty()).replace(tzinfo=None)
    cursor.execute("""
        UPDATE pbx_calls
           SET state = 'ended', ended_at = COALESCE(ended_at, %s), updated_at = NOW()
         WHERE state <> 'ended' AND started_at >= %s
           AND NOT (linkedid = ANY(%s))
    """, (moment, moment - timedelta(days=1), list(known_linkedids or [])))
    return cursor.rowcount or 0


def live_calls(cursor, ended_within_seconds=90, now=None):
    """Идущие звонки и законченные не раньше стольких секунд назад — свежие первыми."""
    moment = (now or now_almaty()).replace(tzinfo=None)
    cursor.execute("""
        SELECT %s FROM pbx_calls
         WHERE (state <> 'ended' AND started_at >= %%s)
            OR (state = 'ended' AND ended_at >= %%s)
         ORDER BY started_at DESC
         LIMIT 300
    """ % ', '.join(CALL_FIELDS), (moment - timedelta(days=1),
                                   moment - timedelta(seconds=int(ended_within_seconds))))
    return [_row_to_call(row) for row in cursor.fetchall()]


def call_by_linkedid(cursor, linkedid):
    cursor.execute("SELECT %s FROM pbx_calls WHERE linkedid = %%s" % ', '.join(CALL_FIELDS),
                   (str(linkedid or '')[:64],))
    row = cursor.fetchone()
    return _row_to_call(row) if row else None


def _row_to_call(row):
    call = dict(zip(CALL_FIELDS, row))
    ringing = call.get('ringing')
    if isinstance(ringing, str):
        ringing = json.loads(ringing or '[]')
    call['ringing'] = list(ringing or [])
    for field in _TIME_FIELDS:
        call[field] = _stamp(call.get(field))
    return call


def last_prefix_for_ext(cursor, ext, days=30):
    """Последний исходящий маршрут, которым набирал этот номер: с него и перезванивать, если
    линию не выбрали."""
    cursor.execute("""
        SELECT dial_prefix FROM pbx_calls
         WHERE ext = %s AND direction = 'out' AND dial_prefix <> '' AND started_at >= %s
         ORDER BY started_at DESC LIMIT 1
    """, (str(ext or '')[:8], now_almaty().replace(tzinfo=None) - timedelta(days=int(days))))
    row = cursor.fetchone()
    return row[0] if row else ''


def user_ext(cursor, user_id):
    """Внутренний номер станции у сотрудника: карточка сотрудника, иначе профиль оператора —
    тот же источник, что у справочника «Касаний»."""
    cursor.execute("""
        SELECT COALESCE(NULLIF(TRIM(u.sip_number), ''), NULLIF(TRIM(op.sip_number), ''))
          FROM users u LEFT JOIN operator_profiles op ON op.user_id = u.id
         WHERE u.id = %s
    """, (int(user_id),))
    row = cursor.fetchone()
    value = str(row[0] or '').strip() if row else ''
    return value if pbx_calls._EXT_RE.match(value) else ''


# ── команды ──────────────────────────────────────────────────────────────────

def enqueue_command(cursor, kind, params, requested_by=None, linkedid=None):
    cursor.execute("""
        INSERT INTO pbx_commands (kind, params, requested_by, linkedid)
        VALUES (%s, %s, %s, %s) RETURNING id
    """, (str(kind)[:16], Json(dict(params or {})), requested_by,
          (str(linkedid)[:64] if linkedid else None)))
    return int(cursor.fetchone()[0])


def claim_commands(cursor, limit=10):
    """Команды коннектору: queued → sent. Протухшие (коннектор не приходил дольше
    COMMAND_TTL_SECONDS) — expired, и на станцию они не уходят."""
    cursor.execute("""
        UPDATE pbx_commands SET status = 'expired', finished_at = NOW(),
               message = 'коннектор не забрал команду вовремя'
         WHERE status = 'queued' AND created_at < NOW() - make_interval(secs => %s)
    """, (int(COMMAND_TTL_SECONDS),))
    cursor.execute("""
        UPDATE pbx_commands SET status = 'sent', sent_at = NOW()
         WHERE id IN (SELECT id FROM pbx_commands WHERE status = 'queued'
                       ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED)
        RETURNING id, kind, params
    """, (int(limit),))
    out = []
    for command_id, kind, params in cursor.fetchall():
        if isinstance(params, str):
            params = json.loads(params or '{}')
        item = {'id': int(command_id), 'kind': kind}
        item.update(params or {})
        out.append(item)
    return out


def finish_command(cursor, command_id, status, message='', linkedid=None):
    """Итог от коннектора. accepted — промежуточный (телефон звонит); done/failed — конечный.
    Конечный итог не перезаписывается промежуточным, пришедшим позже из-за порядка пачек."""
    status = str(status or '')
    if status not in ('accepted', 'done', 'failed'):
        return False
    cursor.execute("""
        UPDATE pbx_commands
           SET status = %(status)s,
               message = COALESCE(NULLIF(%(message)s, ''), message),
               linkedid = COALESCE(%(linkedid)s, linkedid),
               finished_at = CASE WHEN %(status)s IN ('done', 'failed') THEN NOW() ELSE finished_at END
         WHERE id = %(id)s
           AND (status IN ('queued', 'sent', 'accepted') OR %(status)s IN ('done', 'failed'))
           AND NOT (status IN ('done', 'failed') AND %(status)s = 'accepted')
    """, {'status': status, 'message': str(message or '')[:500],
          'linkedid': (str(linkedid)[:64] if linkedid else None), 'id': int(command_id)})
    return bool(cursor.rowcount)


def command(cursor, command_id):
    cursor.execute("""
        SELECT id, kind, params, requested_by, status, message, linkedid, created_at, finished_at
          FROM pbx_commands WHERE id = %s
    """, (int(command_id),))
    row = cursor.fetchone()
    if not row:
        return None
    params = row[2] if isinstance(row[2], dict) else json.loads(row[2] or '{}')
    return {'id': int(row[0]), 'kind': row[1], 'params': params, 'requested_by': row[3],
            'status': row[4], 'message': row[5], 'linkedid': row[6],
            'created_at': row[7].isoformat() if row[7] else None,
            'finished_at': row[8].isoformat() if row[8] else None}


# ── уборка ───────────────────────────────────────────────────────────────────

def cleanup_due(cursor, hours=12):
    cursor.execute("SELECT cleaned_at FROM pbx_connector_state WHERE id = 1")
    row = cursor.fetchone()
    if row and row[0] and row[0] > datetime.now(row[0].tzinfo) - timedelta(hours=hours):
        return False
    cursor.execute("""
        INSERT INTO pbx_connector_state (id, cleaned_at) VALUES (1, NOW())
        ON CONFLICT (id) DO UPDATE SET cleaned_at = NOW()
    """)
    return True


def drop_expired(cursor):
    cursor.execute("DELETE FROM pbx_calls WHERE started_at < %s",
                   (now_almaty().replace(tzinfo=None) - timedelta(days=PBX_CALLS_RETENTION_DAYS),))
    calls = cursor.rowcount or 0
    cursor.execute("DELETE FROM pbx_commands WHERE created_at < NOW() - make_interval(days => %s)",
                   (PBX_COMMANDS_RETENTION_DAYS,))
    return calls + (cursor.rowcount or 0)
