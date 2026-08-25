# -*- coding: utf-8 -*-
"""SQL-слой раздела «Касания».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют ни
пулом, ни транзакцией — их держит вызывающий. Так же устроены wiki, crm,
parcels и call_qa.

Здесь это важно вдвойне: перезапись суток — это DELETE и INSERT по одной и той
же дате. Возьми каждая функция своё соединение, и посреди синхронизации сутки
на секунду оказались бы пустыми — ровно в тот момент, когда кто-то читает
таблицу.
"""

import json
from datetime import datetime, timedelta

from psycopg2.extras import Json, execute_values

from . import access, sync
from .schema import RETENTION_DAYS

# Смещение Алматы от UTC. Render живёт в UTC, и «сегодня» у него до 06:00 по
# Алматы ещё вчерашнее. Считаем сдвигом, а не ZoneInfo: у Казахстана с
# 01.03.2024 одна зона без перевода часов, а tzdata на контейнере может и не
# оказаться (тот же приём в parcels/queries.py).
_ALMATY_OFFSET = timedelta(hours=5)


def now_almaty():
    return datetime.utcnow() + _ALMATY_OFFSET


def today_almaty():
    return now_almaty().date()


# ─────────────────────────────────────────────────────────────────────────────
# Контекст доступа
# ─────────────────────────────────────────────────────────────────────────────

_ACCESS_CONTEXT_SQL = """
WITH me AS (
    SELECT id, name, role, department_id FROM users WHERE id = %(user_id)s
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
    cursor.execute(_ACCESS_CONTEXT_SQL, {'user_id': int(user_id)})
    row = cursor.fetchone()
    if not row or row[1] is None:
        return None
    name, role, department_id, department_code, headed_ids, headed_codes = row
    return {
        'user_id': int(user_id),
        'name': name,
        'role': access.normalize_role(role),
        'department_id': department_id,
        'department_code': department_code,
        'headed_department_ids': list(headed_ids or []),
        'headed_department_codes': list(headed_codes or []),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Справочник внутренних номеров
# ─────────────────────────────────────────────────────────────────────────────

# Номер берётся из карточки сотрудника, а если там пусто — из профиля оператора.
# Один человек может встретиться дважды, и один номер — за двумя людьми: это не
# ошибка выборки, а живое состояние базы (номер уволившегося отдают новому).
_DB_OPERATORS_SQL = """
SELECT COALESCE(NULLIF(TRIM(u.sip_number), ''), NULLIF(TRIM(op.sip_number), '')) AS ext,
       u.name, u.hire_date, d.name AS direction, dp.code AS department
  FROM users u
  LEFT JOIN operator_profiles op ON op.user_id = u.id
  LEFT JOIN directions d  ON d.id  = COALESCE(u.direction_id, op.direction_id)
  LEFT JOIN departments dp ON dp.id = COALESCE(u.department_id, d.department_id)
 WHERE COALESCE(NULLIF(TRIM(u.sip_number), ''), NULLIF(TRIM(op.sip_number), '')) IS NOT NULL
"""


def db_operator_rows(cursor):
    cursor.execute(_DB_OPERATORS_SQL)
    return [{'ext': row[0], 'name': row[1], 'hire_date': row[2],
             'direction': row[3], 'department': row[4]} for row in cursor.fetchall()]


def save_directory(cursor, directory):
    """Перезаписывает справочник целиком.

    Именно перезаписывает, а не доливает: пропавший из обоих источников номер
    должен исчезнуть и отсюда, иначе он останется подписан человеком, которого
    там уже нет. Касания на него не ссылаются внешним ключом — на них лежит
    только сам номер, — так что удалять безопасно.
    """
    cursor.execute("DELETE FROM cdr_operators")
    if not directory:
        return 0
    payload = [(ext, Json(record['periods']), record.get('station') or '',
                record.get('source') or '') for ext, record in directory.items()]
    execute_values(cursor, """
        INSERT INTO cdr_operators (ext, periods, station, source)
        VALUES %s
    """, payload)
    return len(payload)


def load_directory(cursor):
    cursor.execute("SELECT ext, periods, station, source FROM cdr_operators")
    out = {}
    for ext, periods, station, source in cursor.fetchall():
        if isinstance(periods, str):
            periods = json.loads(periods)
        out[str(ext)] = {'periods': periods or [], 'station': station, 'source': source}
    return out


def directory_updated_at(cursor):
    cursor.execute("SELECT MAX(updated_at) FROM cdr_operators")
    row = cursor.fetchone()
    return row[0] if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Состояние выкачки по суткам
# ─────────────────────────────────────────────────────────────────────────────

def day_states(cursor, day_from, day_to):
    cursor.execute("""
        SELECT day, status, rows_fetched, touches, complete, claimed_at,
               finished_at, attempts, error
          FROM cdr_sync_days
         WHERE day BETWEEN %s AND %s
         ORDER BY day
    """, (day_from, day_to))
    return [{
        'day': row[0].isoformat(), 'status': row[1], 'rows_fetched': row[2],
        'touches': row[3], 'complete': row[4],
        'claimed_at': row[5].isoformat() if row[5] else None,
        'finished_at': row[6].isoformat() if row[6] else None,
        'attempts': row[7], 'error': row[8],
    } for row in cursor.fetchall()]


def enqueue_days(cursor, days, requested_by=None):
    """Ставит сутки в очередь мосту.

    Уже стоящие в очереди не трогаются: повторный вход в раздел не должен
    сбрасывать счётчик попыток и не должен отбирать задание у моста, который
    прямо сейчас его выполняет.
    """
    if not days:
        return 0
    cursor.execute("""
        INSERT INTO cdr_sync_days (day, status, requested_at, requested_by)
        SELECT d, 'pending', NOW(), %(by)s
          FROM unnest(%(days)s::date[]) AS d
        ON CONFLICT (day) DO UPDATE
           SET status = 'pending', requested_at = NOW(), error = NULL,
               claimed_at = NULL, claimed_by = NULL
         WHERE cdr_sync_days.status <> 'running'
            OR cdr_sync_days.claimed_at IS NULL
            OR cdr_sync_days.claimed_at < NOW() - make_interval(mins => %(stale)s)
    """, {'days': list(days), 'by': requested_by, 'stale': sync.STALE_MINUTES})
    return cursor.rowcount or 0


def claim_days(cursor, agent_id, limit=1):
    """Мост забирает следующие сутки в работу.

    `FOR UPDATE SKIP LOCKED` — чтобы два экземпляра моста (например, старый не
    добили, а новый уже поставили) не взяли одни и те же сутки. Свежие идут
    первыми: человек, который прямо сейчас смотрит на прогресс, ждёт последние
    дни, а не позапрошлый месяц.
    """
    cursor.execute("""
        WITH next AS (
            SELECT day FROM cdr_sync_days
             WHERE attempts < %(max_attempts)s
               AND (status = 'pending'
                    OR (status = 'running'
                        AND claimed_at < NOW() - make_interval(mins => %(stale)s)))
             ORDER BY day DESC
             LIMIT %(limit)s
             FOR UPDATE SKIP LOCKED
        )
        UPDATE cdr_sync_days d
           SET status = 'running', claimed_at = NOW(), claimed_by = %(agent)s,
               attempts = d.attempts + 1, error = NULL
          FROM next
         WHERE d.day = next.day
     RETURNING d.day, d.attempts
    """, {'stale': sync.STALE_MINUTES, 'limit': int(limit),
          'max_attempts': sync.MAX_ATTEMPTS, 'agent': str(agent_id or '')[:120]})
    return [{'day': row[0].isoformat(), 'attempts': row[1]} for row in cursor.fetchall()]


def mark_day_done(cursor, day, rows_fetched, touches, complete):
    cursor.execute("""
        INSERT INTO cdr_sync_days (day, status, rows_fetched, touches, complete,
                                   finished_at, attempts)
        VALUES (%s, 'done', %s, %s, %s, NOW(), 1)
        ON CONFLICT (day) DO UPDATE
           SET status = 'done', rows_fetched = EXCLUDED.rows_fetched,
               touches = EXCLUDED.touches, complete = EXCLUDED.complete,
               finished_at = NOW(), error = NULL,
               -- Счётчик обнуляем: он считает попытки ПОДРЯД. Иначе сутки,
               -- которые каждый день дочитываются штатно (сегодняшние), за
               -- неделю упрутся в потолок и перестанут обновляться совсем.
               attempts = 0
    """, (day, int(rows_fetched), int(touches), bool(complete)))


def mark_day_error(cursor, day, error):
    cursor.execute("""
        INSERT INTO cdr_sync_days (day, status, finished_at, error, attempts)
        VALUES (%s, 'error', NOW(), %s, 1)
        ON CONFLICT (day) DO UPDATE
           SET status = 'error', finished_at = NOW(), error = EXCLUDED.error,
               attempts = cdr_sync_days.attempts + 1
         -- Закрытые сутки отказом не портим: мост мог не справиться с ПОВТОРНЫМ
         -- чтением уже собранных суток, и терять из-за этого готовые данные
         -- (а вместе с ними и строку статуса, которую видит человек) незачем.
         WHERE cdr_sync_days.status <> 'done' OR NOT cdr_sync_days.complete
    """, (day, str(error)[:500]))


def replace_day_touches(cursor, day, touches):
    """Кладёт касания суток, снеся прежние.

    DELETE + INSERT, а не UPSERT: у звонка мог поменяться linkedid-состав
    (станция дописала плечи), и прежняя строка осталась бы сиротой. Обе операции
    в одной транзакции вызывающего, так что пустых суток снаружи не видно.
    """
    cursor.execute("DELETE FROM cdr_touches WHERE call_day = %s", (day,))
    if not touches:
        return 0
    payload = [(
        str(touch['linkedid'])[:64], touch['phone'][:16], day,
        touch['started_at'], touch['answered_at'] or None,
        (touch['ext'] or '')[:8], touch['call_type'][:32], touch['result'][:32],
        int(touch['talk_seconds']), int(touch['dial_seconds']),
        (touch['queue'] or '')[:64], touch['recording_url'] or None,
        min(int(touch['legs']), 32000),
    ) for touch in touches]
    execute_values(cursor, """
        INSERT INTO cdr_touches (
            linkedid, phone, call_day, started_at, answered_at, ext, call_type,
            result, talk_seconds, dial_seconds, queue, recording_url, legs)
        VALUES %s
        ON CONFLICT (linkedid, phone) DO UPDATE SET
            call_day = EXCLUDED.call_day, started_at = EXCLUDED.started_at,
            answered_at = EXCLUDED.answered_at, ext = EXCLUDED.ext,
            call_type = EXCLUDED.call_type, result = EXCLUDED.result,
            talk_seconds = EXCLUDED.talk_seconds, dial_seconds = EXCLUDED.dial_seconds,
            queue = EXCLUDED.queue, recording_url = EXCLUDED.recording_url,
            legs = EXCLUDED.legs
    """, payload, page_size=1000)
    return len(payload)


# ─────────────────────────────────────────────────────────────────────────────
# Состояние моста
# ─────────────────────────────────────────────────────────────────────────────

def agent_seen(cursor, *, hostname=None, version=None, station_url=None,
               error=None, days_sent=0, rows_read=0):
    """Отметка «мост на связи». Зовётся на каждом его запросе.

    Счётчики накопительные: по ним видно, работает мост или просто здоровается.
    Ошибка НЕ затирается пустой при следующем удачном заходе — рядом лежит её
    время, и «последняя ошибка вчера в 3 ночи» это другой разговор, чем
    «последняя ошибка минуту назад».
    """
    cursor.execute("""
        INSERT INTO cdr_agent_state (id, last_seen_at, hostname, version,
                                     station_url, last_error, last_error_at,
                                     days_sent, rows_read)
        VALUES (1, NOW(), %(host)s, %(version)s, %(station)s, %(error)s,
                CASE WHEN %(error)s IS NULL THEN NULL ELSE NOW() END,
                %(days)s, %(rows)s)
        ON CONFLICT (id) DO UPDATE SET
            last_seen_at  = NOW(),
            hostname      = COALESCE(EXCLUDED.hostname, cdr_agent_state.hostname),
            version       = COALESCE(EXCLUDED.version, cdr_agent_state.version),
            station_url   = COALESCE(EXCLUDED.station_url, cdr_agent_state.station_url),
            last_error    = COALESCE(EXCLUDED.last_error, cdr_agent_state.last_error),
            last_error_at = CASE WHEN %(error)s IS NULL
                                 THEN cdr_agent_state.last_error_at ELSE NOW() END,
            days_sent     = cdr_agent_state.days_sent + %(days)s,
            rows_read     = cdr_agent_state.rows_read + %(rows)s
    """, {'host': hostname, 'version': version, 'station': station_url,
          'error': (str(error)[:500] if error else None),
          'days': int(days_sent), 'rows': int(rows_read)})


def agent_state(cursor):
    cursor.execute("""
        SELECT last_seen_at, hostname, version, station_url, last_error,
               last_error_at, days_sent, rows_read, agents_at
          FROM cdr_agent_state WHERE id = 1
    """)
    row = cursor.fetchone()
    if not row:
        return {'connected': False, 'last_seen_at': None}
    return {
        'connected': bool(row[0]),
        'last_seen_at': row[0].isoformat() if row[0] else None,
        'hostname': row[1], 'version': row[2], 'station_url': row[3],
        'last_error': row[4],
        'last_error_at': row[5].isoformat() if row[5] else None,
        'days_sent': row[6], 'rows_read': row[7],
        'agents_at': row[8].isoformat() if row[8] else None,
    }


def save_station_agents(cursor, agents):
    """Справочник агентов станции, присланный мостом.

    Лежит сырым: справочник номеров пересобирается и тогда, когда мост молчит,
    а второй раз просить его об этом было бы не у кого.
    """
    cursor.execute("""
        INSERT INTO cdr_agent_state (id, station_agents, agents_at)
        VALUES (1, %s, NOW())
        ON CONFLICT (id) DO UPDATE
           SET station_agents = EXCLUDED.station_agents, agents_at = NOW()
    """, (Json(agents or {}),))


def load_station_agents(cursor):
    cursor.execute("SELECT station_agents FROM cdr_agent_state WHERE id = 1")
    row = cursor.fetchone()
    value = row[0] if row else None
    if isinstance(value, str):
        value = json.loads(value)
    return {str(k): str(v or '') for k, v in (value or {}).items()}


def drop_expired(cursor, retention_days=RETENTION_DAYS):
    """Убирает кэш старше срока хранения. Это кэш: понадобится — мост принесёт
    заново. Зовётся не чаще раза в сутки (см. `cleanup_due`)."""
    edge = today_almaty() - timedelta(days=int(retention_days))
    cursor.execute("DELETE FROM cdr_touches WHERE call_day < %s", (edge,))
    removed = cursor.rowcount or 0
    cursor.execute("DELETE FROM cdr_sync_days WHERE day < %s", (edge,))
    cursor.execute("""
        INSERT INTO cdr_agent_state (id, cleaned_at) VALUES (1, NOW())
        ON CONFLICT (id) DO UPDATE SET cleaned_at = NOW()
    """)
    return removed


def cleanup_due(cursor, hours=24):
    """Пора ли убираться. Отдельным запросом, а не по расписанию: своего
    планировщика у раздела нет, а мост стучится и так — уборку вешаем на его
    холостой заход, когда работы всё равно нет."""
    cursor.execute("""
        SELECT cleaned_at IS NULL
            OR cleaned_at < NOW() - make_interval(hours => %s)
          FROM cdr_agent_state WHERE id = 1
    """, (int(hours),))
    row = cursor.fetchone()
    return True if row is None else bool(row[0])


# ─────────────────────────────────────────────────────────────────────────────
# Чтение касаний
# ─────────────────────────────────────────────────────────────────────────────

_FILTER_SQL = """
  FROM cdr_touches t
 WHERE t.call_day BETWEEN %(day_from)s AND %(day_to)s
   AND (%(call_type)s IS NULL OR t.call_type = %(call_type)s)
   AND (%(result)s    IS NULL OR t.result    = %(result)s)
   AND (%(ext)s       IS NULL OR t.ext       = %(ext)s)
   AND (%(queue)s     IS NULL OR t.queue LIKE '%%' || %(queue)s || '%%')
   AND (%(phone)s     IS NULL OR t.phone LIKE '%%' || %(phone)s || '%%')
   AND (NOT %(talked_only)s OR t.talk_seconds > 0)
"""


def _params(day_from, day_to, filters):
    filters = filters or {}
    return {
        'day_from': day_from, 'day_to': day_to,
        'call_type': filters.get('call_type') or None,
        'result': filters.get('result') or None,
        'ext': filters.get('ext') or None,
        'queue': filters.get('queue') or None,
        'phone': filters.get('phone') or None,
        'talked_only': bool(filters.get('talked_only')),
    }


_COLUMNS = ("t.started_at, t.answered_at, t.phone, t.ext, t.call_type, t.result, "
            "t.talk_seconds, t.dial_seconds, t.queue, t.recording_url, "
            "t.linkedid, t.legs")


def _row_to_touch(row):
    return {
        'started_at': row[0].strftime('%Y-%m-%d %H:%M:%S') if row[0] else '',
        'answered_at': row[1].strftime('%Y-%m-%d %H:%M:%S') if row[1] else '',
        'phone': row[2], 'ext': row[3] or '', 'call_type': row[4], 'result': row[5],
        'talk_seconds': row[6], 'dial_seconds': row[7], 'queue': row[8] or '',
        'recording_url': row[9] or '', 'has_recording': bool(row[9]),
        'linkedid': row[10], 'legs': row[11],
    }


def count_touches(cursor, day_from, day_to, filters=None):
    cursor.execute("SELECT COUNT(*) " + _FILTER_SQL, _params(day_from, day_to, filters))
    return int(cursor.fetchone()[0])


def select_touches(cursor, day_from, day_to, filters=None, limit=100, offset=0):
    params = _params(day_from, day_to, filters)
    params['limit'] = int(limit)
    params['offset'] = int(offset)
    cursor.execute(
        "SELECT " + _COLUMNS + _FILTER_SQL +
        " ORDER BY t.started_at, t.phone LIMIT %(limit)s OFFSET %(offset)s", params)
    return [_row_to_touch(row) for row in cursor.fetchall()]


def iter_touches(cursor, day_from, day_to, filters=None, chunk=5000):
    """Все касания периода порциями — для выгрузки.

    ОДИН запрос и `fetchmany`, а не страницы LIMIT/OFFSET. Причина не в скорости:
    соединение работает в READ COMMITTED, и каждая новая страница видела бы свой
    снимок. Мост в это время может перезаписать сутки периода (сегодняшние он
    дочитывает постоянно) — строки сдвинутся, и файл получит одни касания дважды,
    а другие потеряет. Один SELECT читается из одного снимка целиком.

    Порциями — чтобы не держать месяц в памяти дважды: здесь списком и в openpyxl.
    """
    cursor.execute(
        "SELECT " + _COLUMNS + _FILTER_SQL + " ORDER BY t.started_at, t.linkedid, t.phone",
        _params(day_from, day_to, filters))
    while True:
        rows = cursor.fetchmany(int(chunk))
        if not rows:
            return
        for row in rows:
            yield _row_to_touch(row)


def summary(cursor, day_from, day_to, filters=None):
    """Сводка периода одним запросом — карточки над таблицей."""
    cursor.execute("""
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE t.talk_seconds > 0),
               COUNT(*) FILTER (WHERE t.call_type = 'Исходящий'),
               COUNT(*) FILTER (WHERE t.call_type = 'Входящий'),
               COUNT(*) FILTER (WHERE t.call_type = 'Входящий (не приняли)'),
               COALESCE(SUM(t.talk_seconds), 0),
               COUNT(DISTINCT t.ext) FILTER (WHERE t.ext <> ''),
               COUNT(DISTINCT t.phone),
               COUNT(*) FILTER (WHERE t.recording_url IS NOT NULL)
    """ + _FILTER_SQL, _params(day_from, day_to, filters))
    row = cursor.fetchone() or (0,) * 9
    return {
        'total': int(row[0]), 'talks': int(row[1]), 'outgoing': int(row[2]),
        'incoming': int(row[3]), 'incoming_missed': int(row[4]),
        'talk_seconds': int(row[5]), 'operators': int(row[6]),
        'phones': int(row[7]), 'with_recording': int(row[8]),
    }


def operator_stats(cursor, day_from, day_to, filters=None):
    """Разрез по внутренним номерам, с точностью ДО СУТОК.

    ФИО подставляется выше по справочнику: на касании его нет намеренно (см.
    schema.py). Группируем по (номер, сутки), а не по одному номеру на весь
    период, именно из-за справочника: номер уволившегося отдают новому
    сотруднику, и если период захватывает день передачи, весь номер записался бы
    на одного человека. Суток в периоде максимум 92, номеров — десятки, так что
    строк тут тысячи, а не миллионы.
    """
    cursor.execute("""
        SELECT t.ext,
               t.call_day,
               COUNT(*),
               COUNT(*) FILTER (WHERE t.talk_seconds > 0),
               COALESCE(SUM(t.talk_seconds), 0),
               COUNT(DISTINCT t.phone)
    """ + _FILTER_SQL + """
         GROUP BY t.ext, t.call_day
         ORDER BY t.ext, t.call_day
    """, _params(day_from, day_to, filters))
    return [{
        'ext': row[0] or '', 'day': row[1].isoformat(),
        'touches': int(row[2]), 'talks': int(row[3]),
        'talk_seconds': int(row[4]), 'phones': int(row[5]),
    } for row in cursor.fetchall()]


def breakdown(cursor, day_from, day_to, column, filters=None):
    """Разрезы «по типу» и «по результату» — считаются базой, а не при обходе
    строк: выгрузка стримит касания генератором и второго прохода по ним нет.

    column приходит не от пользователя, а из фиксированного набора — иначе это
    была бы подстановка в SQL.
    """
    if column not in ('call_type', 'result'):
        raise ValueError('breakdown: неизвестный разрез %r' % column)
    cursor.execute("SELECT t.%s, COUNT(*) " % column + _FILTER_SQL +
                   " GROUP BY t.%s ORDER BY COUNT(*) DESC" % column,
                   _params(day_from, day_to, filters))
    return [(row[0], int(row[1])) for row in cursor.fetchall()]


def daily_stats(cursor, day_from, day_to, filters=None):
    cursor.execute("""
        SELECT t.call_day,
               COUNT(*),
               COUNT(*) FILTER (WHERE t.talk_seconds > 0),
               COALESCE(SUM(t.talk_seconds), 0)
    """ + _FILTER_SQL + """
         GROUP BY t.call_day
         ORDER BY t.call_day
    """, _params(day_from, day_to, filters))
    return [{'day': row[0].isoformat(), 'touches': int(row[1]),
             'talks': int(row[2]), 'talk_seconds': int(row[3])}
            for row in cursor.fetchall()]


def filter_values(cursor, day_from, day_to):
    """Что реально встречается в периоде — для выпадающих списков. Показывать в
    фильтре значение, которого в данных нет, значит обещать пустой результат."""
    cursor.execute("""
        SELECT DISTINCT result FROM cdr_touches
         WHERE call_day BETWEEN %s AND %s ORDER BY 1
    """, (day_from, day_to))
    results = [row[0] for row in cursor.fetchall() if row[0]]
    cursor.execute("""
        SELECT DISTINCT queue FROM cdr_touches
         WHERE call_day BETWEEN %s AND %s AND queue <> '' ORDER BY 1
    """, (day_from, day_to))
    queues = sorted({part for row in cursor.fetchall()
                     for part in str(row[0]).split(',') if part})
    return {'results': results, 'queues': queues}
