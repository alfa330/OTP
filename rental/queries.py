# -*- coding: utf-8 -*-
"""SQL раздела «Аренда авто». Время в таблицах — наивное время Алматы (rules.ALMATY)."""

import json
from datetime import datetime

from psycopg2.extras import execute_values

from . import rules

# Сбор идёт секунды; «собирается» дольше этого — значит, процесс перезапустили посреди сбора.
STALE_COLLECTING_MINUTES = 10


def now_almaty():
    return datetime.now(rules.ALMATY).replace(tzinfo=None)


_ACCESS_CONTEXT_SQL = """
    SELECT u.name, u.role, u.status, d.code,
           ARRAY(SELECT lower(h.code) FROM departments h
                  WHERE h.head_user_id = u.id AND COALESCE(h.is_active, TRUE) AND h.code IS NOT NULL)
      FROM users u
      LEFT JOIN departments d ON d.id = u.department_id
     WHERE u.id = %(user_id)s
"""


def load_access_context(cursor, user_id):
    """Профиль для прав раздела одним запросом. None — нет учётки."""
    cursor.execute(_ACCESS_CONTEXT_SQL, {'user_id': int(user_id)})
    row = cursor.fetchone()
    if not row:
        return None
    name, role, status, department_code, headed = row
    return {
        'user_id': int(user_id),
        'name': name or '',
        'role': str(role or '').strip().lower(),
        'status': status,
        'department_code': str(department_code or '').strip().lower() or None,
        'headed_codes': [code for code in (headed or ()) if code],
    }


# ─── Отчёты ────────────────────────────────────────────────────────────────────

_REPORT_COLUMNS = ("r.id, r.kind, r.slot_at, r.status, r.collected_at, r.sources, r.error, r.created_by, "
                   "r.started_at, r.updated_at")


def _report(row):
    sources = row[5]
    if isinstance(sources, str):
        sources = json.loads(sources or '{}')
    return {'id': int(row[0]), 'kind': row[1], 'slot_at': row[2], 'status': row[3],
            'collected_at': row[4], 'sources': sources or {}, 'error': row[6],
            'created_by': row[7], 'started_at': row[8], 'updated_at': row[9]}


def expire_stale(cursor, now):
    """«Собирается» дольше 10 минут — сбор оборвал перезапуск: помечаем отказом, чтобы
    кнопка «Обновить» снова стала доступна, а не ждала вечно."""
    cursor.execute("""
        UPDATE rental_night_reports
           SET status = 'failed', error = 'сбор прервался — соберите заново', updated_at = %s
         WHERE status = 'collecting' AND started_at < %s - (%s * interval '1 minute')
    """, (now, now, STALE_COLLECTING_MINUTES))


def latest_report(cursor):
    cursor.execute("SELECT " + _REPORT_COLUMNS + " FROM rental_night_reports r ORDER BY r.slot_at DESC, r.id DESC LIMIT 1")
    row = cursor.fetchone()
    return _report(row) if row else None


def report_bounds(cursor):
    """Первый и последний слоты истории — границы календаря на экране. (None, None) — отчётов нет."""
    cursor.execute("SELECT MIN(slot_at), MAX(slot_at) FROM rental_night_reports")
    first, last = cursor.fetchone()
    return first, last


def get_report(cursor, report_id, *, for_update=False):
    cursor.execute("SELECT " + _REPORT_COLUMNS + " FROM rental_night_reports r WHERE r.id = %s"
                   + (" FOR UPDATE" if for_update else ""), (int(report_id),))
    row = cursor.fetchone()
    return _report(row) if row else None


def find_report(cursor, kind, slot):
    cursor.execute("SELECT " + _REPORT_COLUMNS + " FROM rental_night_reports r WHERE r.kind = %s AND r.slot_at = %s",
                   (kind, slot))
    row = cursor.fetchone()
    return _report(row) if row else None


def claim_report(cursor, kind, slot, created_by, now):
    """Отчёт слота в состояние «собирается». (отчёт, занят ли уже другим сбором).

    Строка слота одна (UNIQUE kind, slot_at): нет — заводим, есть — переводим в
    «собирается», если она не собирается прямо сейчас. Строка блокируется до конца
    транзакции, поэтому два одновременных «Собрать» не начнут два сбора.
    """
    cursor.execute("""
        INSERT INTO rental_night_reports (kind, slot_at, status, created_by, started_at, created_at, updated_at)
        VALUES (%s, %s, 'collecting', %s, %s, %s, %s)
        ON CONFLICT (kind, slot_at) DO NOTHING
        RETURNING id
    """, (kind, slot, created_by, now, now, now))
    created = cursor.fetchone()
    if created:
        return get_report(cursor, created[0]), False
    report = find_report(cursor, kind, slot)
    report = get_report(cursor, report['id'], for_update=True)
    if report['status'] == 'collecting':
        return report, True
    cursor.execute("""
        UPDATE rental_night_reports SET status = 'collecting', error = NULL, started_at = %s, updated_at = %s
         WHERE id = %s
    """, (now, now, report['id']))
    return get_report(cursor, report['id']), False


def finish_report(cursor, report_id, *, status, sources, error, collected_at, now):
    cursor.execute("""
        UPDATE rental_night_reports
           SET status = %s, sources = %s::jsonb, error = %s, collected_at = COALESCE(%s, collected_at),
               updated_at = %s
         WHERE id = %s
    """, (status, json.dumps(sources or {}, ensure_ascii=False), error, collected_at, now, int(report_id)))


# ─── Строки ────────────────────────────────────────────────────────────────────

_ROW_COLUMNS = ("id, plate, gps_site, gps_sites, gps_status, signal_at, address, lat, lon, rent_status, "
                "in_fleet, model, state, state_source, state_by, state_at")


def _row(row):
    item = dict(zip([name.strip() for name in _ROW_COLUMNS.split(',')], row))
    item['gps_sites'] = [s for s in (item.get('gps_sites') or '').split(',') if s]
    return item


def rows_for_report(cursor, report_id):
    cursor.execute("SELECT " + _ROW_COLUMNS + " FROM rental_night_rows WHERE report_id = %s",
                   (int(report_id),))
    rows = [_row(row) for row in cursor.fetchall()]
    rows.sort(key=rules.sort_key)
    return rows


def has_rows(cursor, report_id):
    cursor.execute("SELECT EXISTS (SELECT 1 FROM rental_night_rows WHERE report_id = %s)", (int(report_id),))
    return bool(cursor.fetchone()[0])


def get_row(cursor, row_id, *, for_update=False):
    cursor.execute("SELECT " + _ROW_COLUMNS + ", report_id FROM rental_night_rows WHERE id = %s"
                   + (" FOR UPDATE" if for_update else ""), (int(row_id),))
    row = cursor.fetchone()
    if not row:
        return None
    item = _row(row[:-1])
    item['report_id'] = int(row[-1])
    return item


def save_rows(cursor, report_id, rows):
    """Строки сбора в отчёт. Ручное «Состояние» прежнего сбора того же отчёта остаётся.

    Пересбор («Обновить») обновляет данные GPS и Yume у каждой машины; подставленное по
    адресу считается заново, поставленное рукой — нет. Машина пропала из GPS — её строка
    уходит, если у неё не было ручного состояния; с ручным — остаётся как была.
    """
    cursor.execute("SELECT plate, state, state_by, state_at FROM rental_night_rows "
                   "WHERE report_id = %s AND state_source = %s", (int(report_id), rules.STATE_BY_HAND))
    manual = {plate: (state, by, at) for plate, state, by, at in cursor.fetchall()}
    fresh = {row['plate'] for row in rows}
    cursor.execute("DELETE FROM rental_night_rows WHERE report_id = %s AND NOT (plate = ANY(%s)) "
                   "AND COALESCE(state_source, '') <> %s", (int(report_id), list(fresh), rules.STATE_BY_HAND))
    values = []
    for row in rows:
        state, source, by, at = row.get('state'), row.get('state_source'), None, None
        if row['plate'] in manual:
            state, by, at = manual[row['plate']]
            source = rules.STATE_BY_HAND
        values.append((
            int(report_id), row['plate'], row.get('gps_site'), ','.join(row.get('gps_sites') or ()),
            row['gps_status'], rules.almaty_from_ts(row.get('signal_ts')), row.get('address'),
            row.get('lat'), row.get('lon'), row.get('rent_status'), bool(row.get('in_fleet')),
            (row.get('model') or None) and str(row['model'])[:120], state, source, by, at,
        ))
    if values:
        execute_values(cursor, """
            INSERT INTO rental_night_rows (report_id, plate, gps_site, gps_sites, gps_status, signal_at, address,
                                           lat, lon, rent_status, in_fleet, model, state, state_source,
                                           state_by, state_at)
            VALUES %s
            ON CONFLICT (report_id, plate) DO UPDATE SET
                gps_site = EXCLUDED.gps_site, gps_sites = EXCLUDED.gps_sites, gps_status = EXCLUDED.gps_status,
                signal_at = EXCLUDED.signal_at, address = EXCLUDED.address, lat = EXCLUDED.lat,
                lon = EXCLUDED.lon, rent_status = EXCLUDED.rent_status, in_fleet = EXCLUDED.in_fleet,
                model = EXCLUDED.model, state = EXCLUDED.state, state_source = EXCLUDED.state_source,
                state_by = EXCLUDED.state_by, state_at = EXCLUDED.state_at
        """, values, page_size=500)
    return len(values)


def set_state(cursor, row, state, ctx, now):
    """Ручное «Состояние» строки и запись в журнал. state=None — снять."""
    cursor.execute("""
        UPDATE rental_night_rows SET state = %s, state_source = %s, state_by = %s, state_at = %s
         WHERE id = %s
    """, (state, rules.STATE_BY_HAND, ctx['user_id'], now, int(row['id'])))
    cursor.execute("""
        INSERT INTO rental_night_state_log (report_id, plate, old_state, new_state, changed_by,
                                            changed_by_name, changed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (row['report_id'], row['plate'], row.get('state'), state, ctx['user_id'], ctx.get('name') or '', now))


def user_names(cursor, ids):
    ids = sorted({int(x) for x in ids if x})
    if not ids:
        return {}
    cursor.execute("SELECT id, name FROM users WHERE id = ANY(%s)", (ids,))
    return {int(user_id): name or '' for user_id, name in cursor.fetchall()}
