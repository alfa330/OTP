# -*- coding: utf-8 -*-
"""SQL для «CR%» и «ОСС» «Отчёта по входу» (cdr/inbound_staffing.py): состав группы,
смены из «Графиков работы», события телефонов и звонки, по которым виден человек.

Все функции принимают готовый курсор и ничего не пишут.
"""

from test_numbers import keys as test_keys

from . import touches as touches_mod


def roster(cursor, model, day_from, day_to):
    """id операторов, которые в периоде состояли в группе с моделью расчёта `model`."""
    cursor.execute("""
        SELECT DISTINCT m.operator_id
          FROM group_operator_memberships m
          JOIN groups g ON g.id = m.group_id
         WHERE LOWER(COALESCE(g.calculation_model_code, '')) = %s
           AND m.start_date <= %s
           AND (m.end_date IS NULL OR m.end_date >= %s)
    """, (model, day_to, day_from))
    return sorted(int(row[0]) for row in cursor.fetchall())


def memberships(cursor, operator_ids, day_from, day_to):
    """{operator_id: [{id, start_date, end_date, group_id, model}]} — ВСЕ членства состава,
    задевшие период, а не только нужной группы: по ним решается, в какой группе человек
    был в конкретный день (перевели — смена уже не этой группы)."""
    cursor.execute("""
        SELECT m.operator_id, m.id, m.start_date, m.end_date, g.id,
               LOWER(COALESCE(g.calculation_model_code, ''))
          FROM group_operator_memberships m
          JOIN groups g ON g.id = m.group_id
         WHERE m.operator_id = ANY(%s)
           AND m.start_date <= %s
           AND (m.end_date IS NULL OR m.end_date >= %s)
    """, (list(operator_ids), day_to, day_from))
    out = {}
    for operator_id, member_id, start, end, group_id, model in cursor.fetchall():
        out.setdefault(int(operator_id), []).append({
            'id': int(member_id), 'start_date': start, 'end_date': end,
            'group_id': int(group_id), 'model': model or ''})
    return out


def operator_exts(cursor, operator_ids):
    """{operator_id: внутренний номер} — нынешний: истории номеров у портала нет."""
    cursor.execute("SELECT id, NULLIF(TRIM(sip_number), '') FROM users WHERE id = ANY(%s)",
                   (list(operator_ids),))
    return {int(row[0]): str(row[1]) for row in cursor.fetchall() if row[1]}


def operator_names(cursor, operator_ids):
    """{operator_id: ФИО} — для листа «Смены»: чья смена засчитана, а чья нет."""
    cursor.execute("SELECT id, name FROM users WHERE id = ANY(%s)", (list(operator_ids),))
    return {int(row[0]): row[1] or '' for row in cursor.fetchall()}


def scheduled_shifts(cursor, operator_ids, day_from, day_to):
    """Смены из «Графиков работы» с началом в периоде и их перерывы.

    Перерывы — парами минут от полуночи дня смены, как их хранит `shift_breaks`."""
    cursor.execute("""
        SELECT s.operator_id, s.shift_date, s.start_time, s.end_time, s.shift_type,
               COALESCE(array_agg(b.start_minutes ORDER BY b.start_minutes, b.id)
                        FILTER (WHERE b.id IS NOT NULL), '{}'),
               COALESCE(array_agg(b.end_minutes ORDER BY b.start_minutes, b.id)
                        FILTER (WHERE b.id IS NOT NULL), '{}')
          FROM work_shifts s
          LEFT JOIN shift_breaks b ON b.shift_id = s.id
         WHERE s.operator_id = ANY(%s)
           AND s.shift_date BETWEEN %s AND %s
         GROUP BY s.id
    """, (list(operator_ids), day_from, day_to))
    return [{
        'operator_id': int(row[0]), 'day': row[1], 'start_time': row[2], 'end_time': row[3],
        'shift_type': row[4] or 'regular',
        'breaks': list(zip(row[5] or (), row[6] or ())),
    } for row in cursor.fetchall()]


def phone_events(cursor, operator_ids, since, until):
    """{operator_id: [(время, ключ статуса)]} — живые события iCORE Phone по возрастанию.

    Только события самого телефона (`client_event_id IS NOT NULL`): в той же таблице лежат
    импорты, а они не про этот телефон. Индекс (operator_id, event_at) есть."""
    cursor.execute("""
        SELECT operator_id, event_at, status_key
          FROM operator_status_events
         WHERE operator_id = ANY(%s)
           AND client_event_id IS NOT NULL
           AND event_at >= %s AND event_at < %s
         ORDER BY operator_id, event_at, id
    """, (list(operator_ids), since, until))
    out = {}
    for operator_id, event_at, status_key in cursor.fetchall():
        out.setdefault(int(operator_id), []).append((event_at, status_key))
    return out


def human_call_moments(cursor, exts, day_from, day_to):
    """{внутренний номер: [начала звонков по возрастанию]} — звонки, которые делает человек:
    исходящий и принятый входящий. По ним отчёт о сменах отличает человека у телефона от
    телефона, который сам вернул статус после сна компьютера."""
    exts = sorted({str(ext) for ext in exts or () if ext})
    if not exts:
        return {}
    cursor.execute("""
        SELECT ext, started_at
          FROM cdr_touches
         WHERE call_day BETWEEN %s AND %s
           AND ext = ANY(%s)
           AND call_type IN (%s, %s)
           AND """ + test_keys.sql_not_test('cdr_touches.phone', digits=True) + """
         ORDER BY ext, started_at
    """, (day_from, day_to, exts, touches_mod.TYPE_OUT, touches_mod.TYPE_IN))
    out = {}
    for ext, started_at in cursor.fetchall():
        out.setdefault(str(ext), []).append(started_at)
    return out
