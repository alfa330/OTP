# -*- coding: utf-8 -*-
"""SQL-слой раздела «АТС». Функции принимают готовый курсор (Database._get_cursor) и не
управляют транзакцией — её держит вызывающий, как в cdr/queries.py.

Ловушки, учтённые здесь
-----------------------
* Пересылка живого состояния идёт пачками и после обрыва может прийти второй раз в старом
  виде. Поэтому законченный звонок никогда не «оживает»: запись с state='ended' обновляется
  только записью с тем же состоянием. Порядковый номер коннектора (seq) для этого не годится
  — после его перезапуска нумерация начинается заново.
* Строка переписывается и получает новую ревизию (`rev`), только если в ней что-то
  изменилось: полный снимок раз в минуту присылает все звонки заново, и без этого сравнения
  каждый снимок будил бы всех зрителей пачкой «изменений», которых не было.
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

# Поля звонка в порядке коннектора (cdr/pbx_calls.CALL_FIELDS) — один список на обе стороны.
CALL_FIELDS = pbx_calls.CALL_FIELDS
# То, что читается из базы: поля звонка и его ревизия.
READ_FIELDS = CALL_FIELDS + ('rev',)
# Поля, по которым решается «строка изменилась»: всё, кроме номера коннектора.
_COMPARED_FIELDS = tuple(f for f in CALL_FIELDS if f not in ('linkedid', 'seq'))

_TIME_FIELDS = ('started_at', 'queued_at', 'ringing_at', 'answered_at', 'ended_at')

# Начало «живых» суток: старше этого незаконченный звонок — уже не живой, а потерянный отбой.
LIVE_WINDOW = timedelta(days=1)
# Коннектор подтверждает команду (accepted/failed) за секунду; отправленная без подтверждения
# дольше этого считается потерянной в оборванном канале. Экран следит за командой ~40 с.
SENT_ACK_SECONDS = 30


def _ts(value):
    """«YYYY-MM-DDTHH:MM:SS» → datetime или None. Мусор — None: лучше пустая ячейка, чем
    упавшая пачка."""
    text = str(value or '')[:19]
    if len(text) != 19:
        return None
    try:
        return datetime.strptime(text.replace(' ', 'T'), '%Y-%m-%dT%H:%M:%S')
    except ValueError:
        return None


def _stamp(value):
    if value is None:
        return ''
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%dT%H:%M:%S')
    return str(value)[:19].replace(' ', 'T')


def _int_or_none(value):
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
    if direction not in pbx_calls.DIRECTIONS:
        direction = ''
    ringing = item.get('ringing') or []
    if not isinstance(ringing, list):
        ringing = []
    ringing = sorted({str(x)[:8] for x in ringing if str(x).strip()})[:20]
    side = str(item.get('hangup_side') or '')
    return {
        'linkedid': linkedid,
        'direction': direction,
        'phone': touches_mod.norm_phone(item.get('phone')),
        'did': str(item.get('did') or '')[:24],
        'queue': str(item.get('queue') or '')[:8],
        'ext': str(item.get('ext') or '')[:8],
        'peer_ext': str(item.get('peer_ext') or '')[:8],
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
        'command_id': _int_or_none(item.get('command_id')),
        'seq': _int_or_none(item.get('seq')) or 0,
        # Восстановлен коннектором из списка каналов станции (вход в AMI): точных моментов в нём
        # нет — запись не должна затирать то, что база уже знает (upsert_calls, слияние).
        'partial': bool(item.get('partial')),
    }


def row_to_call(row):
    """Строка `SELECT READ_FIELDS` → словарь звонка; время строками, `ringing` списком."""
    call = dict(zip(READ_FIELDS, row))
    ringing = call.get('ringing')
    if isinstance(ringing, str):
        ringing = json.loads(ringing or '[]')
    call['ringing'] = list(ringing or [])
    for field in _TIME_FIELDS:
        call[field] = _stamp(call.get(field))
    call['rev'] = int(call.get('rev') or 0)
    return call


_SELECT_CALLS = 'SELECT ' + ', '.join(READ_FIELDS) + ' FROM pbx_calls'


# ── коннектор ────────────────────────────────────────────────────────────────

def connector_seen(cursor, *, hostname=None, version=None, agent_key=None, ami=None,
                   snapshot=False, stream=False):
    """Отметка «коннектор на связи». Возвращает прежнее ami_connected (None — строки не было):
    по нему ручка решает, сообщать ли зрителям, что коннектор потерял или нашёл станцию.

    `ami` = None — запрос пришёл по каналу команд, а не с пачкой событий: состояние станции
    он не несёт и затирать его нельзя."""
    has_ami = ami is not None
    ami = ami or {}
    cursor.execute("""
        WITH old AS (SELECT ami_connected FROM pbx_connector_state WHERE id = 1)
        INSERT INTO pbx_connector_state (id, last_seen_at, hostname, version, agent_key,
                                         ami_connected, ami_host, ami_error, events_total,
                                         snapshot_at, stream_at)
        VALUES (1, NOW(), %(host)s, %(version)s, %(key)s, %(ami_ok)s, %(ami_host)s, %(ami_error)s,
                %(events)s, CASE WHEN %(snapshot)s THEN NOW() ELSE NULL END,
                CASE WHEN %(stream)s THEN NOW() ELSE NULL END)
        ON CONFLICT (id) DO UPDATE SET
            last_seen_at  = NOW(),
            hostname      = COALESCE(EXCLUDED.hostname, pbx_connector_state.hostname),
            version       = COALESCE(EXCLUDED.version, pbx_connector_state.version),
            agent_key     = COALESCE(EXCLUDED.agent_key, pbx_connector_state.agent_key),
            ami_connected = CASE WHEN %(has_ami)s THEN EXCLUDED.ami_connected
                                 ELSE pbx_connector_state.ami_connected END,
            ami_host      = COALESCE(EXCLUDED.ami_host, pbx_connector_state.ami_host),
            ami_error     = CASE WHEN %(has_ami)s THEN EXCLUDED.ami_error
                                 ELSE pbx_connector_state.ami_error END,
            events_total  = GREATEST(EXCLUDED.events_total, pbx_connector_state.events_total),
            snapshot_at   = CASE WHEN %(snapshot)s THEN NOW() ELSE pbx_connector_state.snapshot_at END,
            stream_at     = CASE WHEN %(stream)s THEN NOW() ELSE pbx_connector_state.stream_at END
        RETURNING (SELECT ami_connected FROM old)
    """, {'host': (str(hostname)[:120] if hostname else None),
          'version': (str(version)[:40] if version else None),
          'key': (str(agent_key)[:32] if agent_key else None),
          'has_ami': has_ami,
          'ami_ok': bool(ami.get('connected')),
          'ami_host': (str(ami.get('host'))[:120] if ami.get('host') else None),
          'ami_error': (str(ami.get('error'))[:500] if ami.get('error') else None),
          'events': int(ami.get('events_total') or 0),
          'snapshot': bool(snapshot),
          'stream': bool(stream)})
    row = cursor.fetchone()
    return row[0] if row else None


def save_lines(cursor, lines):
    cursor.execute("""
        INSERT INTO pbx_connector_state (id, lines, lines_at) VALUES (1, %s, NOW())
        ON CONFLICT (id) DO UPDATE SET lines = EXCLUDED.lines, lines_at = NOW()
    """, (Json(lines),))


def connector_state(cursor):
    cursor.execute("""
        SELECT last_seen_at, hostname, version, agent_key, ami_connected, ami_host, ami_error,
               events_total, snapshot_at, lines, lines_at, stream_at
          FROM pbx_connector_state WHERE id = 1
    """)
    row = cursor.fetchone()
    if not row:
        return {'last_seen_at': None, 'ami_connected': False, 'lines': {}, 'stream_at': None}
    lines = row[9] if isinstance(row[9], dict) else json.loads(row[9] or '{}')
    return {
        'last_seen_at': row[0].isoformat() if row[0] else None,
        'hostname': row[1], 'version': row[2], 'agent_key': row[3],
        'ami_connected': bool(row[4]), 'ami_host': row[5], 'ami_error': row[6],
        'events_total': row[7],
        'snapshot_at': row[8].isoformat() if row[8] else None,
        'lines': lines or {},
        'lines_at': row[10].isoformat() if row[10] else None,
        'stream_at': row[11].isoformat() if row[11] else None,
    }


# ── звонки ───────────────────────────────────────────────────────────────────

# Слияние «частичного» звонка (восстановлен из списка каналов при входе коннектора в AMI) с тем,
# что уже лежит в базе. Список каналов не знает моментов: вход в очередь, звон, ответ коннектор
# в нём ставит «сейчас», а набор исходящего — по CallerIDNum транка. Поэтому:
#   текст — новое, если оно не пустое, иначе прежнее (направление, номер, линия, канал…);
#   моменты, начало и ожидание — прежние, если известны (они точные), иначе новые;
#   состояние, кто звонит, конец и причина — новые (это «сейчас»).
_MERGE_TEXT = ('direction', 'phone', 'did', 'queue', 'ext', 'peer_ext', 'hangup_side', 'dial_prefix',
               'dial_status', 'channel', 'agent_channel', 'recording_file', 'origin')
_MERGE_KEEP_KNOWN = ('queued_at', 'ringing_at', 'answered_at', 'wait_seconds', 'command_id')


def _merge_expression(field):
    if field in _MERGE_TEXT:
        return "COALESCE(NULLIF(EXCLUDED.%s, ''), pbx_calls.%s)" % (field, field)
    if field in _MERGE_KEEP_KNOWN:
        return 'COALESCE(pbx_calls.%s, EXCLUDED.%s)' % (field, field)
    if field == 'started_at':
        # Известное начало точное (Newchannel), а из списка — «сейчас минус Duration» с
        # округлением до секунды: у существующей строки остаётся известное.
        return 'pbx_calls.started_at'
    if field == 'hangup_cause':
        return 'COALESCE(EXCLUDED.hangup_cause, pbx_calls.hangup_cause)'
    return 'EXCLUDED.%s' % field


def _upsert(cursor, calls, merge):
    rows = [tuple(Json(call['ringing']) if field == 'ringing' else call[field] for field in CALL_FIELDS)
            for call in calls]
    columns = ', '.join(CALL_FIELDS) + ', rev'
    template = '(' + ', '.join(['%s'] * len(CALL_FIELDS)) + ", nextval('pbx_calls_rev_seq'))"
    value = _merge_expression if merge else (lambda field: 'EXCLUDED.%s' % field)
    updates = ', '.join('%s = %s' % (f, value(f)) for f in CALL_FIELDS if f != 'linkedid')
    changed = ' OR '.join('pbx_calls.%s IS DISTINCT FROM %s' % (f, value(f)) for f in _COMPARED_FIELDS)
    result = execute_values(cursor, """
        INSERT INTO pbx_calls (%s) VALUES %%s
        ON CONFLICT (linkedid) DO UPDATE SET %s, rev = EXCLUDED.rev, updated_at = NOW()
        WHERE (pbx_calls.state <> 'ended' OR EXCLUDED.state = 'ended')
          AND (%s)
        RETURNING linkedid
    """ % (columns, updates, changed), rows, template=template, fetch=True, page_size=500)
    return [row[0] for row in result or []]


def upsert_calls(cursor, calls):
    """Пачка состояний от коннектора → список linkedid строк, которые действительно изменились
    (им выдана новая ревизия). Законченный звонок не оживает: строка с state='ended' принимает
    только state='ended' (у второго прихода старой пачки после обрыва). «Частичные» звонки
    (восстановлены из списка каналов) сливаются с известным, а не затирают его."""
    if not calls:
        return []
    full = [call for call in calls if not call.get('partial')]
    partial = [call for call in calls if call.get('partial')]
    out = []
    if full:
        out += _upsert(cursor, full, merge=False)
    if partial:
        out += _upsert(cursor, partial, merge=True)
    return out


def close_missing(cursor, known_linkedids, now=None):
    """После полного снимка: незаконченные у нас звонки, которых в снимке нет, закрываются —
    событие отбоя потерялось. Только свежие (сутки): старее — уже уборка. Возвращает их число."""
    moment = (now or now_almaty()).replace(tzinfo=None)
    cursor.execute("""
        UPDATE pbx_calls
           SET state = 'ended', ended_at = COALESCE(ended_at, %s), ringing = '[]',
               rev = nextval('pbx_calls_rev_seq'), updated_at = NOW()
         WHERE state <> 'ended' AND started_at >= %s
           AND NOT (linkedid = ANY(%s))
    """, (moment, moment - LIVE_WINDOW, list(known_linkedids or [])))
    return cursor.rowcount or 0


def calls_since(cursor, rev, limit=1000):
    """Изменения новее ревизии `rev` — то, что слушатель реалтайма раздаёт зрителям."""
    cursor.execute(_SELECT_CALLS + " WHERE rev > %s ORDER BY rev LIMIT %s", (int(rev or 0), int(limit)))
    return [row_to_call(row) for row in cursor.fetchall()]


def max_rev(cursor):
    cursor.execute("SELECT COALESCE(MAX(rev), 0) FROM pbx_calls")
    row = cursor.fetchone()
    return int(row[0] or 0) if row else 0


def live_calls(cursor, ended_within_seconds=90, now=None):
    """Идущие звонки и законченные не раньше стольких секунд назад — свежие первыми."""
    moment = (now or now_almaty()).replace(tzinfo=None)
    cursor.execute(_SELECT_CALLS + """
         WHERE (state <> 'ended' AND started_at >= %s)
            OR (state = 'ended' AND ended_at >= %s)
         ORDER BY started_at DESC
         LIMIT 300
    """, (moment - LIVE_WINDOW, moment - timedelta(seconds=int(ended_within_seconds))))
    return [row_to_call(row) for row in cursor.fetchall()]


def active_calls(cursor, now=None):
    """Только идущие звонки (не законченные), начатые за последние сутки, — для табло: кто в
    очереди, у кого соединение, кто говорит. Законченные здесь не нужны: их итог — в касаниях."""
    moment = (now or now_almaty()).replace(tzinfo=None)
    cursor.execute(_SELECT_CALLS + """
         WHERE state <> 'ended' AND started_at >= %s
         ORDER BY started_at
         LIMIT 500
    """, (moment - LIVE_WINDOW,))
    return [row_to_call(row) for row in cursor.fetchall()]


def call_by_linkedid(cursor, linkedid):
    cursor.execute(_SELECT_CALLS + " WHERE linkedid = %s", (str(linkedid or '')[:64],))
    row = cursor.fetchone()
    return row_to_call(row) if row else None


def last_prefix_for_ext(cursor, ext, days=PBX_CALLS_RETENTION_DAYS):
    """Последний исходящий маршрут, которым набирал этот номер: с него и перезванивать, если
    линию не выбрали."""
    cursor.execute("""
        SELECT dial_prefix FROM pbx_calls
         WHERE ext = %s AND direction IN ('out', 'autodial') AND dial_prefix <> '' AND started_at >= %s
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
    return value if pbx_calls.EXT_RE.match(value) else ''


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
    COMMAND_TTL_SECONDS) — expired, и на станцию они не уходят.

    Отправленная, но не подтверждённая дольше SENT_ACK_SECONDS — тоже expired: она ушла в канал,
    который оборвался, и коннектор её не получил. Повторно не отдаётся намеренно: лучше «не
    прошло, нажмите ещё раз», чем два звонка клиенту, если подтверждение просто задержалось."""
    cursor.execute("""
        UPDATE pbx_commands SET status = 'expired', finished_at = NOW(),
               message = 'коннектор не забрал команду вовремя'
         WHERE status = 'queued' AND created_at < NOW() - make_interval(secs => %s)
    """, (int(COMMAND_TTL_SECONDS),))
    cursor.execute("""
        UPDATE pbx_commands SET status = 'expired', finished_at = NOW(),
               message = 'коннектор не подтвердил команду — связь оборвалась, попробуйте ещё раз'
         WHERE status = 'sent' AND sent_at < NOW() - make_interval(secs => %s)
    """, (int(SENT_ACK_SECONDS),))
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
    out.sort(key=lambda item: item['id'])
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
