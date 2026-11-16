# -*- coding: utf-8 -*-
"""SQL пульса телефона: весь запросный текст пакета живёт здесь.

Читает две чужие таблицы и ничего в них не пишет: operator_status_events (события статусов,
телефонные — с client_event_id) и user_sessions (сессии входа; у телефона агент «iCORE Phone»,
last_seen_at обновляется при его запросах не чаще раза в пять минут). Пишет сервис — штатным
db.append_operator_status_event, тем же путём, каким события кладёт сам телефон.
"""
from datetime import timedelta

from . import rules


def _key(value):
    return str(value or '').strip().lower()


def load_rows(cursor, department_codes, now, lookback_days=rules.LOOKBACK_DAYS):
    """Последнее живое событие статуса каждого сотрудника отделов, пульс его телефона (pulse) и
    последняя активность любой его сессии (seen).

    В ответе только те, кого обход может тронуть: статус открыт (не «выключен») либо закрыт
    самим сервером. department_codes — коды отделов; '*' — все отделы."""
    codes = sorted({str(code).strip().lower() for code in (department_codes or []) if str(code).strip()})
    if not codes:
        return []
    cursor.execute(
        """
        SELECT u.id, ev.id, ev.key, ev.state_note, ev.event_at,
               (SELECT MAX(s.last_seen_at)
                FROM user_sessions s
                WHERE s.user_id = u.id AND s.user_agent = %s) AS pulse,
               (SELECT MAX(s.last_seen_at)
                FROM user_sessions s
                WHERE s.user_id = u.id) AS seen
        FROM users u
        LEFT JOIN departments d ON d.id = u.department_id
        CROSS JOIN LATERAL (
            SELECT e.id, e.status_key AS key, e.state_note, e.event_at
            FROM operator_status_events e
            WHERE e.operator_id = u.id
              AND e.client_event_id IS NOT NULL
              AND e.event_at > %s
            ORDER BY e.event_at DESC, e.id DESC
            LIMIT 1
        ) ev
        WHERE (%s OR LOWER(COALESCE(d.code, '')) = ANY(%s))
        ORDER BY u.id
        """,
        (rules.PHONE_USER_AGENT, now - timedelta(days=int(lookback_days)), '*' in codes, codes),
    )
    out = []
    for row in cursor.fetchall():
        # Ключ приводим здесь, а не LOWER() в запросе: тот зависит от локали базы и кириллицу
        # в «C» не трогает — «Выключен» сошёл бы за открытый статус.
        item = {'operator_id': int(row[0]), 'event_id': int(row[1]), 'key': _key(row[2]),
                'note': row[3], 'event_at': row[4], 'pulse': row[5], 'seen': row[6]}
        if item['key'] != rules.OFF_KEY or rules.closed_by_server(item):
            out.append(item)
    return out


def freshest_pulse(cursor, now):
    """Самая свежая активность среди сессий всех телефонов — жив ли сам учёт активности."""
    cursor.execute(
        "SELECT MAX(last_seen_at) FROM user_sessions WHERE user_agent = %s AND last_seen_at > %s",
        (rules.PHONE_USER_AGENT, now - timedelta(days=1)),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def previous_status(cursor, row):
    """Статус, который стоял перед закрытием: его сервер и продолжит. None — продолжать нечего."""
    cursor.execute(
        """
        SELECT status_key
        FROM operator_status_events
        WHERE operator_id = %s
          AND client_event_id IS NOT NULL
          AND (event_at, id) < (%s, %s)
        ORDER BY event_at DESC, id DESC
        LIMIT 1
        """,
        (row['operator_id'], row['event_at'], row['event_id']),
    )
    found = cursor.fetchone()
    key = _key(found[0]) if found else ''
    return key if key and key != rules.OFF_KEY else None
