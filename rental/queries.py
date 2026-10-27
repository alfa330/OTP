# -*- coding: utf-8 -*-
"""SQL раздела «Аренда авто». Время в таблицах — наивное время Алматы (rules.ALMATY)."""

import json
from datetime import datetime, timedelta

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


# ═══ Наличие авто и направления (#391) ══════════════════════════════════════════

_CARD_COLUMNS = ('id', 'title', 'car_class', 'price', 'year', 'fuel', 'color', 'age', 'insurance_class',
                 'insurance', 'first_day', 'office', 'yume_keys', 'position', 'is_active',
                 'free_count', 'free_by_name', 'free_at')
_CARD_EDITABLE = ('title', 'car_class', 'price', 'year', 'fuel', 'color', 'age', 'insurance_class',
                  'insurance', 'first_day', 'office', 'yume_keys')


def _card(row):
    card = dict(zip(_CARD_COLUMNS, row))
    card['yume_keys'] = list(card.get('yume_keys') or [])
    return card


def cards(cursor, *, active_only=True):
    cursor.execute("SELECT " + ', '.join(_CARD_COLUMNS) + " FROM rental_cards"
                   + (" WHERE is_active" if active_only else "") + " ORDER BY position, id")
    return [_card(row) for row in cursor.fetchall()]


def get_card(cursor, card_id, *, for_update=False):
    cursor.execute("SELECT " + ', '.join(_CARD_COLUMNS) + " FROM rental_cards WHERE id = %s"
                   + (" FOR UPDATE" if for_update else ""), (int(card_id),))
    row = cursor.fetchone()
    return _card(row) if row else None


def _log_card(cursor, card_id, field, old, new, ctx, now):
    cursor.execute("""
        INSERT INTO rental_card_log (card_id, field, old_value, new_value, changed_by, changed_by_name, changed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (card_id, field, _log_text(old), _log_text(new), ctx['user_id'], ctx.get('name') or '', now))


def _log_text(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return ', '.join(str(item) for item in value)
    return str(value)


def create_card(cursor, card, ctx, now):
    cursor.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM rental_cards")
    position = cursor.fetchone()[0]
    cursor.execute("""
        INSERT INTO rental_cards (title, car_class, price, year, fuel, color, age, insurance_class, insurance,
                                  first_day, office, yume_keys, position, created_by, created_at, updated_at)
        VALUES (%(title)s, %(car_class)s, %(price)s, %(year)s, %(fuel)s, %(color)s, %(age)s, %(insurance_class)s,
                %(insurance)s, %(first_day)s, %(office)s, %(yume_keys)s, %(position)s, %(by)s, %(now)s, %(now)s)
        RETURNING id
    """, dict(card, position=position, by=ctx['user_id'], now=now))
    card_id = cursor.fetchone()[0]
    _log_card(cursor, card_id, 'created', None, card['title'], ctx, now)
    return get_card(cursor, card_id)


def update_card(cursor, current, card, ctx, now):
    """Записать изменённые поля; в журнал — каждое поле отдельной строкой. Возвращает число правок."""
    changed = [key for key in _CARD_EDITABLE if card.get(key) != current.get(key)]
    if not changed:
        return 0
    assignments = ', '.join('%s = %%(%s)s' % (key, key) for key in changed)
    cursor.execute("UPDATE rental_cards SET " + assignments + ", updated_at = %(now)s WHERE id = %(id)s",
                   dict({key: card[key] for key in changed}, now=now, id=current['id']))
    for key in changed:
        _log_card(cursor, current['id'], key, current.get(key), card.get(key), ctx, now)
    return len(changed)


def set_free_count(cursor, current, count, ctx, now):
    """Свободные машины карточки — вручную (ТЗ 3.1): кто и когда, и строка журнала."""
    cursor.execute("""
        UPDATE rental_cards SET free_count = %s, free_by = %s, free_by_name = %s, free_at = %s, updated_at = %s
         WHERE id = %s
    """, (count, ctx['user_id'], ctx.get('name') or '', now, now, current['id']))
    _log_card(cursor, current['id'], 'free_count', current.get('free_count') if current.get('free_at') else None,
              count, ctx, now)


def hide_card(cursor, current, ctx, now):
    cursor.execute("UPDATE rental_cards SET is_active = FALSE, updated_at = %s WHERE id = %s", (now, current['id']))
    _log_card(cursor, current['id'], 'hidden', current['title'], None, ctx, now)


def card_log(cursor, card_id, limit=200):
    cursor.execute("""
        SELECT field, old_value, new_value, changed_by_name, changed_at FROM rental_card_log
         WHERE card_id = %s ORDER BY id DESC LIMIT %s
    """, (int(card_id), int(limit)))
    return [dict(zip(('field', 'old', 'new', 'by', 'at'), row)) for row in cursor.fetchall()]


# ─── Снимок парка Yume ──────────────────────────────────────────────────────────

def fleet_sync(cursor):
    cursor.execute("SELECT cars, synced_at, attempted_at, error FROM rental_fleet_sync WHERE id = 1")
    row = cursor.fetchone()
    if not row:
        return {'cars': [], 'synced_at': None, 'attempted_at': None, 'error': None}
    cars = row[0]
    if isinstance(cars, str):
        cars = json.loads(cars or '[]')
    return {'cars': cars or [], 'synced_at': row[1], 'attempted_at': row[2], 'error': row[3]}


def mark_fleet_attempt(cursor, now):
    cursor.execute("""
        INSERT INTO rental_fleet_sync (id, attempted_at) VALUES (1, %s)
        ON CONFLICT (id) DO UPDATE SET attempted_at = EXCLUDED.attempted_at
    """, (now,))


def save_fleet(cursor, cars, now):
    """Новый снимок. В базу — только ключи модели, статус, «удалена» и подпись: без госномеров и клиентов."""
    slim = [{'keys': car.get('keys') or [], 'status': car.get('status'), 'deleted': bool(car.get('deleted')),
             'label': car.get('label') or ''} for car in cars]
    cursor.execute("""
        INSERT INTO rental_fleet_sync (id, cars, synced_at, attempted_at, error) VALUES (1, %s::jsonb, %s, %s, NULL)
        ON CONFLICT (id) DO UPDATE SET cars = EXCLUDED.cars, synced_at = EXCLUDED.synced_at,
                                       attempted_at = EXCLUDED.attempted_at, error = NULL
    """, (json.dumps(slim, ensure_ascii=False), now, now))


def save_fleet_error(cursor, error, now):
    cursor.execute("""
        INSERT INTO rental_fleet_sync (id, attempted_at, error) VALUES (1, %s, %s)
        ON CONFLICT (id) DO UPDATE SET attempted_at = EXCLUDED.attempted_at, error = EXCLUDED.error
    """, (now, str(error)[:300]))


# ─── Парки ──────────────────────────────────────────────────────────────────────

def sales_parks(cursor):
    """Парки для направления — общий справочник вики («Парки»), пространства отдела продаж.

    Тот же источник, что у «Обращений» (crm/queries.taxi_parks): второй список парков значил бы,
    что оператор выбирает из одного набора, а справочник компании живёт другим. В направлении
    хранится название — реестр читается, даже если парк потом переименуют или уберут.
    """
    cursor.execute("""
        SELECT p.name FROM wiki_taxi_parks p
         WHERE p.status = 'active'
           AND p.space_id IN (SELECT sd.space_id FROM wiki_space_departments sd
                                JOIN departments d ON d.id = sd.department_id
                               WHERE lower(d.code) = %s)
         ORDER BY p.position, p.name
    """, ('op',))
    return [row[0] for row in cursor.fetchall()]


# ─── Направления ────────────────────────────────────────────────────────────────

_DIRECTION_COLUMNS = ('id', 'card_id', 'card_title', 'card_class', 'card_terms', 'office', 'phone', 'park',
                      'visit_at', 'comment', 'status', 'created_by', 'created_by_name', 'created_at',
                      'status_by', 'status_at')


def _direction(row):
    return dict(zip(_DIRECTION_COLUMNS, row))


def card_terms(card):
    """«Путевой лист, от 11 и выше» — по чему отличаются варианты одной модели."""
    return ', '.join(part for part in (card.get('insurance'), card.get('insurance_class')) if part) or None


def create_direction(cursor, fields, card, ctx, visit_at, now):
    cursor.execute("""
        INSERT INTO rental_directions (card_id, card_title, card_class, card_terms, office, phone, park, visit_at,
                                       comment, status, created_by, created_by_name, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'waiting', %s, %s, %s, %s)
        RETURNING id
    """, (card['id'], card['title'], card.get('car_class'), card_terms(card), card.get('office'), fields['phone'],
          fields['park'], visit_at, fields.get('comment'), ctx['user_id'], ctx.get('name') or '', now, now))
    return get_direction(cursor, cursor.fetchone()[0])


def get_direction(cursor, direction_id, *, for_update=False):
    cursor.execute("SELECT " + ', '.join(_DIRECTION_COLUMNS) + " FROM rental_directions WHERE id = %s"
                   + (" FOR UPDATE" if for_update else ""), (int(direction_id),))
    row = cursor.fetchone()
    return _direction(row) if row else None


def directions(cursor, *, own_by=None, date_from=None, date_to=None, status=None, operator_id=None,
               phone_digits=None, limit=50, offset=0):
    """Реестр: свежие визиты сверху. own_by — только направления этого оператора (ТЗ 6)."""
    where, params = [], []
    if own_by is not None:
        where.append('created_by = %s')
        params.append(int(own_by))
    if date_from is not None:
        where.append('visit_at >= %s')
        params.append(datetime(date_from.year, date_from.month, date_from.day))
    if date_to is not None:
        where.append('visit_at < %s')
        params.append(datetime(date_to.year, date_to.month, date_to.day) + timedelta(days=1))
    if status:
        where.append('status = %s')
        params.append(status)
    if operator_id is not None:
        where.append('created_by = %s')
        params.append(int(operator_id))
    if phone_digits:
        where.append("regexp_replace(phone, '\\D', '', 'g') LIKE %s")
        params.append('%' + phone_digits + '%')
    clause = (' WHERE ' + ' AND '.join(where)) if where else ''
    cursor.execute("SELECT COUNT(*) FROM rental_directions" + clause, params)
    total = int(cursor.fetchone()[0])
    cursor.execute("SELECT " + ', '.join(_DIRECTION_COLUMNS) + " FROM rental_directions" + clause
                   + " ORDER BY visit_at DESC, id DESC LIMIT %s OFFSET %s", params + [int(limit), int(offset)])
    return [_direction(row) for row in cursor.fetchall()], total


def direction_operators(cursor):
    """Кто направлял — для отбора «Оператор» в реестре."""
    cursor.execute("""
        SELECT created_by, MAX(created_by_name) FROM rental_directions GROUP BY created_by ORDER BY 2
    """)
    return [{'id': int(row[0]), 'name': row[1] or ''} for row in cursor.fetchall()]


def update_direction(cursor, current, changes, ctx, now):
    """Статус и/или комментарий; каждое изменение — строка журнала (ТЗ 4: кто, когда, было и стало)."""
    changed = {key: value for key, value in changes.items() if value != current.get(key)}
    if not changed:
        return {}
    sets, params = [], []
    for key, value in changed.items():
        sets.append('%s = %%s' % key)
        params.append(value)
    if 'status' in changed:
        sets += ['status_by = %s', 'status_at = %s']
        params += [ctx['user_id'], now]
    cursor.execute("UPDATE rental_directions SET " + ', '.join(sets) + ", updated_at = %s WHERE id = %s",
                   params + [now, current['id']])
    for key, value in changed.items():
        cursor.execute("""
            INSERT INTO rental_direction_log (direction_id, field, old_value, new_value, changed_by,
                                              changed_by_name, changed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (current['id'], key, current.get(key), value, ctx['user_id'], ctx.get('name') or '', now))
    return changed


def direction_log(cursor, direction_id):
    cursor.execute("""
        SELECT field, old_value, new_value, changed_by_name, changed_at FROM rental_direction_log
         WHERE direction_id = %s ORDER BY id
    """, (int(direction_id),))
    return [dict(zip(('field', 'old', 'new', 'by', 'at'), row)) for row in cursor.fetchall()]


# ─── Условия работы ─────────────────────────────────────────────────────────────

def _blocks(value):
    return json.loads(value) if isinstance(value, str) else (value or [])


def conditions_current(cursor):
    cursor.execute("SELECT id, blocks, created_by_name, created_at FROM rental_conditions ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': int(row[0]), 'blocks': _blocks(row[1]), 'by': row[2], 'at': row[3]}


def conditions_versions(cursor, limit=100):
    cursor.execute("SELECT id, created_by_name, created_at FROM rental_conditions ORDER BY id DESC LIMIT %s",
                   (int(limit),))
    return [{'id': int(row[0]), 'by': row[1], 'at': row[2]} for row in cursor.fetchall()]


def conditions_version(cursor, version_id):
    cursor.execute("SELECT id, blocks, created_by_name, created_at FROM rental_conditions WHERE id = %s",
                   (int(version_id),))
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': int(row[0]), 'blocks': _blocks(row[1]), 'by': row[2], 'at': row[3]}


def save_conditions(cursor, blocks, ctx, now):
    cursor.execute("""
        INSERT INTO rental_conditions (blocks, created_by, created_by_name, created_at)
        VALUES (%s::jsonb, %s, %s, %s) RETURNING id
    """, (json.dumps(blocks, ensure_ascii=False), ctx['user_id'], ctx.get('name') or '', now))
    return conditions_version(cursor, cursor.fetchone()[0])
