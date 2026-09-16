# -*- coding: utf-8 -*-
"""Снимок «Табло ОП»: итоги дня, разрезы и люди — из касаний и статусов телефонов.

Определения — те же, что на табло СЗоВ, иначе одна и та же величина горела бы на
двух стенах разным цветом:

  * входящие («дошедшие») — касания типов «Входящий» и «Входящий (не приняли)»;
  * AR = потеряно / входящих; коридор нормы задаётся снаружи (у СЗоВ 3–5 %);
  * SL = отвечено не позже порога / входящих; порог — снаружи (по умолчанию 20 с);
  * среднее время разговора УСЕКАЕТСЯ, не округляется — иначе расходится с отчётом
    владельца ровно на секунду в каждой строке;
  * «онлайн» = свободные + в разговоре; перерыв, тренинг, тех.причина, исход — не онлайн.

Здесь нет ни базы, ни Flask: на вход — списки словарей, на выход — словарь снимка.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from cdr import touches as touches_mod

DEFAULT_SL_SECONDS = 20
DEFAULT_AR_MIN_PERCENT = 3
DEFAULT_AR_MAX_PERCENT = 5

# Ключи оформления статусов, которые считаются «онлайн». Тот же смысл, что у СЗоВ.
ONLINE_STATUS_KEYS = ('free', 'talking')
# Что на табло зовётся «на перерыве» одной цифрой: любой сознательный уход с линии.
PAUSE_STATUS_KEYS = ('break', 'tech', 'training')

_INCOMING_TYPES = (touches_mod.TYPE_IN, touches_mod.TYPE_IN_MISSED)


# Смещение Алматы от UTC — сдвигом, а не ZoneInfo, как в cdr/queries.py: у Казахстана
# с 01.03.2024 одна зона без перевода часов, а tzdata на контейнере может не оказаться.
_ALMATY = timezone(timedelta(hours=5))


def _parse(text):
    """Отметка из базы или моста → наивное время Алматы.

    `cdr_agent_state` хранит TIMESTAMPTZ, а сессия Postgres на Render живёт в UTC, поэтому
    `live_at` приезжает строкой «2026-09-15T20:18:53+00:00». Раньше хвост с поясом просто
    срезался, и UTC сравнивался с алматинским «сейчас»: табло писало «данные на 20:18:53»
    вместо 01:18:53 и «последнее обновление 5:00:10 назад» при живом мосте, а отбивка
    считала мост замолчавшим. Отметки без пояса (касания) уже местные и не трогаются."""
    raw = str(text or '').strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        try:
            value = datetime.strptime(raw[:19].replace('T', ' '), '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None
    if value.tzinfo is not None:
        value = value.astimezone(_ALMATY).replace(tzinfo=None)
    return value


def _local_stamp(value):
    """Отметка для экрана: время Алматы без пояса. formatClock на фронте берёт часы
    из строки как есть и ничего не переводит — UTC он показал бы как местное."""
    parsed = _parse(value)
    return parsed.strftime('%Y-%m-%dT%H:%M:%S') if parsed else None


def _ratio(numerator, denominator):
    return (numerator / denominator) if denominator else None


def _bucket():
    return {'arrived': 0, 'answered': 0, 'missed': 0, 'served_sl': 0,
            'talk_seconds': 0, 'wait_seconds': 0, 'waited': 0,
            'outgoing': 0, 'outgoing_answered': 0}


def _finish(bucket):
    """Дописать производные к сырым счётчикам разреза."""
    out = dict(bucket)
    out['ar'] = _ratio(bucket['missed'], bucket['arrived'])
    out['sl'] = _ratio(bucket['served_sl'], bucket['arrived'])
    # Усечение, а не округление: см. докстринг модуля.
    out['avg_talk_seconds'] = (bucket['talk_seconds'] // bucket['answered']
                               if bucket['answered'] else None)
    out['avg_wait_seconds'] = (bucket['wait_seconds'] // bucket['waited']
                               if bucket['waited'] else None)
    out.pop('waited', None)
    return out


def aggregate(touches, sl_seconds=DEFAULT_SL_SECONDS):
    """Итоги, разрез по часам, счётчики по внутренним номерам.

    Разреза по линиям (очередям станции) нет — решение владельца 16.09.2026: номера
    очередей вида 3010 на стене и в отбивке читались как шум. Поле `queue` касания
    на итоги не влияет."""
    totals = _bucket()
    hourly = [dict(hour=h, arrived=0, answered=0, missed=0, outgoing=0) for h in range(24)]
    by_ext = defaultdict(lambda: {'answered': 0, 'missed': 0, 'outgoing': 0,
                                  'outgoing_answered': 0, 'talk_seconds': 0,
                                  'calls': 0, 'last_call_at': ''})

    for touch in touches:
        started = _parse(touch.get('started_at'))
        if started is None:
            continue
        call_type = touch.get('call_type') or ''
        talk = int(touch.get('talk_seconds') or 0)
        ext = str(touch.get('ext') or '')
        hour = hourly[started.hour]
        person = by_ext[ext] if ext else None
        if person is not None:
            person['calls'] += 1
            person['last_call_at'] = max(person['last_call_at'], touch.get('started_at') or '')

        if call_type == touches_mod.TYPE_OUT:
            answered = talk > 0
            totals['outgoing'] += 1
            totals['outgoing_answered'] += 1 if answered else 0
            hour['outgoing'] += 1
            if person is not None:
                person['outgoing'] += 1
                person['outgoing_answered'] += 1 if answered else 0
                person['talk_seconds'] += talk if answered else 0
            continue

        if call_type not in _INCOMING_TYPES:
            continue
        answered = call_type == touches_mod.TYPE_IN and talk > 0
        answered_at = _parse(touch.get('answered_at'))
        wait = int((answered_at - started).total_seconds()) if (answered and answered_at) else None
        totals['arrived'] += 1
        if answered:
            totals['answered'] += 1
            totals['talk_seconds'] += talk
            if wait is not None:
                totals['waited'] += 1
                totals['wait_seconds'] += max(0, wait)
                if wait <= sl_seconds:
                    totals['served_sl'] += 1
        else:
            totals['missed'] += 1
        hour['arrived'] += 1
        hour['answered' if answered else 'missed'] += 1
        if person is not None:
            person['answered' if answered else 'missed'] += 1
            person['talk_seconds'] += talk if answered else 0

    return {
        'totals': _finish(totals),
        'hourly': hourly,
        'by_ext': dict(by_ext),
    }


def _status(entry, live):
    label, key, weight = entry
    return {
        'status_label': label,
        'status_key': key,
        'status_weight': weight,
        'status_seconds': int(live.get('seconds') or 0) if live else None,
        'status_at': str(live.get('event_at') or '') if live else '',
    }


def build_people(people, live_statuses, status_entry, by_ext, resolve_name):
    """Список людей табло: состав отдела с живым статусом и счётчиками дня, плюс
    внутренние номера, которые сегодня звонили, но в составе не нашлись."""
    rows = []
    seen_exts = set()
    for person in people:
        ext = str(person.get('sip_number') or '')
        live = live_statuses.get(person.get('id')) or {}
        entry = status_entry(live.get('status_key'))
        stats = by_ext.get(ext) or {}
        row = {
            'id': person.get('id'), 'name': person.get('name') or '', 'ext': ext,
            'answered': stats.get('answered', 0), 'missed': stats.get('missed', 0),
            'outgoing': stats.get('outgoing', 0),
            'outgoing_answered': stats.get('outgoing_answered', 0),
            'talk_seconds': stats.get('talk_seconds', 0),
            'last_call_at': stats.get('last_call_at', ''),
            'in_roster': True,
        }
        row.update(_status(entry, live))
        rows.append(row)
        if ext:
            seen_exts.add(ext)
    for ext, stats in by_ext.items():
        if not ext or ext in seen_exts:
            continue
        # Звонил, но в составе отдела не числится: показать честно, а не спрятать —
        # это либо чужой номер на линии продаж, либо карточка без sip_number.
        name = resolve_name(ext) if resolve_name else ''
        row = {
            'id': None, 'name': name or ('Номер %s' % ext), 'ext': ext,
            'answered': stats['answered'], 'missed': stats['missed'],
            'outgoing': stats['outgoing'], 'outgoing_answered': stats['outgoing_answered'],
            'talk_seconds': stats['talk_seconds'], 'last_call_at': stats['last_call_at'],
            'in_roster': False,
        }
        row.update(_status(('Нет в составе', 'unknown', 95), {}))
        rows.append(row)
    rows.sort(key=lambda r: (r['status_weight'], r['name']))
    return rows


def count_now(people_rows):
    counts = defaultdict(int)
    for row in people_rows:
        if row.get('in_roster'):
            counts[row['status_key']] += 1
    return {
        'operators_total': sum(1 for r in people_rows if r.get('in_roster')),
        'operators_online': sum(counts[k] for k in ONLINE_STATUS_KEYS),
        'operators_free': counts['free'],
        'operators_talking': counts['talking'],
        'operators_outgoing': counts['outgoing'],
        'operators_on_break': sum(counts[k] for k in PAUSE_STATUS_KEYS),
        'operators_offline': counts['offline'],
        'operators_unknown': counts['unknown'],
        'operators_other': counts['other'],
    }


def assemble(*, day, touches, people, live_statuses, status_entry, resolve_name,
             bridge_state, now, sl_seconds=DEFAULT_SL_SECONDS,
             ar_min_percent=DEFAULT_AR_MIN_PERCENT, ar_max_percent=DEFAULT_AR_MAX_PERCENT):
    """Снимок целиком. `bridge_state` — строка `cdr_agent_state` (live_at, last_seen_at)."""
    parts = aggregate(touches, sl_seconds)
    rows = build_people(people, live_statuses, status_entry, parts['by_ext'], resolve_name)
    live_at = _parse(bridge_state.get('live_at')) if bridge_state else None
    live_age = int((now - live_at).total_seconds()) if live_at else None
    return {
        'day': day.isoformat(),
        'captured_at': now.strftime('%Y-%m-%dT%H:%M:%S'),
        'sl_threshold_seconds': sl_seconds,
        'ar_min_percent': ar_min_percent,
        'ar_max_percent': ar_max_percent,
        'totals': parts['totals'],
        'hourly': parts['hourly'],
        'now': count_now(rows),
        'operators': rows,
        'bridge': {
            'connected': bool(bridge_state and bridge_state.get('connected')),
            'last_seen_at': _local_stamp((bridge_state or {}).get('last_seen_at')),
            'live_at': _local_stamp((bridge_state or {}).get('live_at')),
            'live_age_seconds': live_age,
        },
    }
