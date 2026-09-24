# -*- coding: utf-8 -*-
"""SQL режима «Сделки» раздела «Касания».

Сделки берутся из снимка «Воронки ОП» (`op_funnel_leads`): его ночная выгрузка
уже ходит в amoCRM и в партнёрские ручки СРМ за теми же лидами, и второй поход
за тем же ради другого экрана был бы дублем запросов и второй копией правил
(какие сутки у лида, какой у него статус). С 16.09.2026 снимок хранит и
телефоны контактов amoCRM — до этого у «Основы» их не было вовсе.

Звонки — из `cdr_touches`, по телефонам сделок. Обе таблицы читаются одним
курсором вызывающего, как везде в пакете.
"""

from datetime import datetime, timedelta

from . import touches as touches_mod

# Сколько дней перед периодом заглядываем в звонки. Окно сделки начинается за две
# минуты до её создания — сделка в 00:01 может владеть звонком предыдущих суток.
TOUCH_LOOKBACK_DAYS = 1

_LEAD_SQL = """
SELECT l.source, l.stream_type, l.lead_key, l.work_day, l.user_id,
       u.name AS owner_name,
       COALESCE(NULLIF(TRIM(u.sip_number), ''), NULLIF(TRIM(op.sip_number), '')) AS owner_ext,
       m.external_name AS owner_external_name, l.owner_raw,
       l.full_name, l.phone, l.phones, l.park_name, l.city, l.base_title,
       l.stage_raw, l.call_status, l.dialog_status, l.reason_raw, l.sub_reason_raw,
       l.comment, l.tags, l.utm_source, l.lead_type, l.registered,
       l.created_at, l.taken_at, l.updated_at
  FROM op_funnel_leads l
  LEFT JOIN users u ON u.id = l.user_id
  LEFT JOIN operator_profiles op ON op.user_id = l.user_id
  LEFT JOIN op_funnel_operator_map m
         ON m.source = l.source AND m.external_key = l.owner_raw
 WHERE l.source = %(source)s
   AND l.direction_code = %(direction)s
   AND l.work_day BETWEEN %(day_from)s AND %(day_to)s
   AND (%(park)s      IS NULL OR l.park_name = %(park)s)
   AND (%(city)s      IS NULL OR l.city = %(city)s)
   AND (%(stage)s     IS NULL OR l.stage_raw = %(stage)s
                              OR l.call_status = %(stage)s OR l.dialog_status = %(stage)s)
   AND (%(lead_type)s IS NULL OR l.lead_type = %(lead_type)s)
   AND (%(owner_user)s IS NULL OR l.user_id = %(owner_user)s)
   AND (%(owner_raw)s  IS NULL OR (l.user_id IS NULL AND l.owner_raw = %(owner_raw)s))
   AND (%(phone)s     IS NULL OR l.phones LIKE '%%' || %(phone)s || '%%')
   AND (%(stream_type)s IS NULL OR l.stream_type = %(stream_type)s)
 ORDER BY COALESCE(l.taken_at, l.created_at) DESC, l.lead_key
"""

_LEAD_KEYS = (
    'source', 'stream_type', 'lead_key', 'work_day', 'user_id', 'owner_name', 'owner_ext',
    'owner_external_name', 'owner_raw', 'full_name', 'phone', 'phones', 'park_name', 'city',
    'base_title', 'stage_raw', 'call_status', 'dialog_status', 'reason_raw', 'sub_reason_raw',
    'comment', 'tags', 'utm_source', 'lead_type', 'registered', 'created_at', 'taken_at',
    'updated_at',
)


def _lead_params(source, direction, day_from, day_to, filters):
    filters = filters or {}
    owner_user, owner_raw = None, None
    owner = filters.get('owner')
    if owner:
        kind, _, value = str(owner).partition(':')
        if kind == 'user' and value.isdigit():
            owner_user = int(value)
        elif kind == 'raw' and value:
            owner_raw = value
    return {
        'source': source, 'direction': direction,
        'day_from': day_from, 'day_to': day_to,
        'park': filters.get('park') or None,
        'city': filters.get('city') or None,
        'stage': filters.get('stage') or None,
        'lead_type': filters.get('lead_type') or None,
        'owner_user': owner_user, 'owner_raw': owner_raw,
        'phone': filters.get('phone') or None,
        'stream_type': int(filters['stream_type']) if filters.get('stream_type') else None,
    }


def select_leads(cursor, source, direction, day_from, day_to, filters=None):
    """Сделки источника за период с ответственным и его внутренним номером."""
    cursor.execute(_LEAD_SQL, _lead_params(source, direction, day_from, day_to, filters))
    out = []
    for row in cursor.fetchall():
        item = dict(zip(_LEAD_KEYS, row))
        for key in ('created_at', 'taken_at', 'updated_at'):
            value = item.get(key)
            item[key] = value if isinstance(value, datetime) or value is None else None
        out.append(item)
    return out


_TOUCH_SQL = """
SELECT t.started_at, t.answered_at, t.phone, t.ext, t.call_type, t.result,
       t.talk_seconds, t.dial_seconds, t.queue, t.recording_url, t.linkedid, t.legs
  FROM cdr_touches t
 WHERE t.phone = ANY(%(phones)s)
   AND t.call_day BETWEEN %(day_from)s AND %(day_to)s
   AND t.call_type <> %(before_queue_type)s
 ORDER BY t.started_at, t.linkedid
"""


def select_touches_for_phones(cursor, phones, day_from, day_to):
    """Все касания по списку телефонов за сутки [day_from - запас, day_to].

    Один запрос с массивом, а не по сделке: месяц «Основы» — это 27 тысяч номеров,
    и по одному это были бы 27 тысяч походов в базу.
    """
    phones = sorted({str(p) for p in phones if p})
    if not phones:
        return []
    cursor.execute(_TOUCH_SQL, {
        'phones': phones,
        'day_from': day_from - timedelta(days=TOUCH_LOOKBACK_DAYS),
        'day_to': day_to,
        # Звонок, не дошедший до очереди, сделке не засчитывается: клиент положил трубку
        # на приветствии, до оператора его не довели (решение владельца 24.09.2026).
        'before_queue_type': touches_mod.TYPE_IN_BEFORE_QUEUE,
    })
    out = []
    for row in cursor.fetchall():
        out.append({
            'started_at': row[0], 'answered_at': row[1], 'phone': row[2], 'ext': row[3] or '',
            'call_type': row[4], 'result': row[5], 'talk_seconds': int(row[6] or 0),
            'dial_seconds': int(row[7] or 0), 'queue': row[8] or '',
            'recording_url': row[9] or '', 'linkedid': row[10], 'legs': int(row[11] or 1),
        })
    return out


def filter_values(cursor, source, direction, day_from, day_to):
    """Что реально встречается у сделок периода — для выпадающих списков.
    Значение, которого в данных нет, в фильтре обещает пустой результат."""
    scope = {'source': source, 'direction': direction, 'day_from': day_from, 'day_to': day_to}
    base = ("FROM op_funnel_leads l WHERE l.source = %(source)s AND l.direction_code = %(direction)s "
            "AND l.work_day BETWEEN %(day_from)s AND %(day_to)s")

    def distinct(column):
        cursor.execute("SELECT DISTINCT l.%s %s AND l.%s <> '' ORDER BY 1" % (column, base, column),
                       scope)
        return [row[0] for row in cursor.fetchall() if row[0]]

    stages = distinct('stage_raw')
    # У «Потока» статус — пара «звонок / диалог»; в один список кладём оба поля.
    for column in ('call_status', 'dialog_status'):
        for value in distinct(column):
            if value not in stages:
                stages.append(value)
    cursor.execute("""
        SELECT l.user_id, COALESCE(u.name, m.external_name, l.owner_raw, ''), l.owner_raw, COUNT(*)
          FROM op_funnel_leads l
          LEFT JOIN users u ON u.id = l.user_id
          LEFT JOIN op_funnel_operator_map m
                 ON m.source = l.source AND m.external_key = l.owner_raw
         WHERE l.source = %(source)s AND l.direction_code = %(direction)s
           AND l.work_day BETWEEN %(day_from)s AND %(day_to)s
         GROUP BY 1, 2, 3
         ORDER BY 4 DESC, 2
    """, scope)
    owners = []
    for user_id, label, owner_raw, count in cursor.fetchall():
        if not label and not owner_raw:
            continue
        owners.append({
            'key': ('user:%d' % user_id) if user_id else ('raw:%s' % owner_raw),
            'label': label or owner_raw, 'count': int(count),
        })
    return {
        'parks': distinct('park_name'),
        'cities': distinct('city'),
        'stages': stages,
        'lead_types': distinct('lead_type'),
        'owners': owners,
    }


def lead_days(cursor, source, direction, day_from, day_to):
    """{сутки ISO: сколько сделок в снимке} — по нему видно, за какие дни лиды
    ещё не выгружались (ночная выгрузка «Воронки ОП» идёт в 05:20 за вчера)."""
    cursor.execute("""
        SELECT work_day, COUNT(*) FROM op_funnel_leads
         WHERE source = %s AND direction_code = %s AND work_day BETWEEN %s AND %s
         GROUP BY work_day
    """, (source, direction, day_from, day_to))
    return {row[0].isoformat(): int(row[1]) for row in cursor.fetchall()}


def count_without_phones(cursor, source, direction, day_from, day_to):
    """Сколько записей периода лежат без телефона. У «Основы» это сделки, снятые до
    16.09.2026, когда снимок телефонов ещё не хранил: по ним звонки не найти,
    пока контакты не дочитаны из amoCRM."""
    cursor.execute("""
        SELECT COUNT(*) FROM op_funnel_leads
         WHERE source = %s AND direction_code = %s AND work_day BETWEEN %s AND %s
           AND phones = '' AND phone = ''
    """, (source, direction, day_from, day_to))
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def last_lead_run(cursor, direction):
    """Последний прогон выгрузки лидов направления: когда, чем кончился."""
    cursor.execute("""
        SELECT started_at, finished_at, status, error, period_from, period_to, leads_written
          FROM op_funnel_sync_runs
         WHERE direction_code = %s
         ORDER BY started_at DESC
         LIMIT 1
    """, (direction,))
    row = cursor.fetchone()
    if not row:
        return None
    return {
        'started_at': row[0].isoformat(sep=' ') if row[0] else None,
        'finished_at': row[1].isoformat(sep=' ') if row[1] else None,
        'status': row[2], 'error': row[3],
        'period_from': row[4].isoformat() if row[4] else None,
        'period_to': row[5].isoformat() if row[5] else None,
        'leads_written': int(row[6] or 0),
    }
