"""SQL-слой раздела «Почта».

Функции принимают ГОТОВЫЙ курсор и не управляют транзакцией — её держит вызывающий
(routes.py). Смена статуса меняет карточку и пишет событие в историю: один курсор —
одна транзакция, и статус без следа «кто и когда» остаться не может.

Видимость (_visibility_clause) — та же, что access.can_view; правило меняется в
обоих местах, расхождение ловит tests/test_mail_postgres.py.
"""

import json
from datetime import date, datetime, timedelta

from . import access, rules

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"
_ALMATY_OFFSET = timedelta(hours=5)

# Отделы, чьим справочником офисов пользуется раздел, — те же, что у «Посылок»:
# один и тот же список офисов в обоих разделах.
OFFICE_DEPARTMENT_CODES = ('front_office', 'szov')


def now_almaty():
    """Render живёт в UTC; у Казахстана с 01.03.2024 одна зона без перевода часов."""
    return datetime.utcnow() + _ALMATY_OFFSET


def today_almaty():
    return now_almaty().date()


def _iso(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _num(value):
    if value is None:
        return None
    return float(value)


def _in(values):
    return ', '.join("'%s'" % value for value in values)


# ─── Контекст зрителя ────────────────────────────────────────────────────────

def load_context(cursor, user_id):
    cursor.execute(
        """
        SELECT u.id, u.name, u.role, u.status, u.phone, u.city,
               p.role,
               COALESCE((SELECT array_agg(po.office_id ORDER BY po.office_id)
                           FROM mail_participant_offices po
                          WHERE po.user_id = u.id), '{}')
          FROM users u
          LEFT JOIN mail_participants p ON p.user_id = u.id
         WHERE u.id = %s
        """, (int(user_id),))
    row = cursor.fetchone()
    if not row:
        return None
    _id, name, portal_role, status, phone, city, role, office_ids = row
    if status == 'fired':
        return None
    portal_role = str(portal_role or '').strip().lower()
    return {
        'user_id': int(user_id),
        'name': name,
        'portal_role': portal_role,
        'is_super': portal_role == 'super_admin',
        'role': role if role in rules.ROLES else None,
        'office_ids': [int(x) for x in (office_ids or [])],
        'phone': phone,
        'city': city,
    }


def section_open_for(cursor, user_id, portal_role):
    """Флаг профиля mail_access: суперадмин или участник раздела (пока идёт пилот —
    только суперадмин, access.PILOT_SUPER_ADMIN_ONLY)."""
    if str(portal_role or '').strip().lower() == 'super_admin':
        return True
    if access.PILOT_SUPER_ADMIN_ONLY:
        return False
    cursor.execute("SELECT 1 FROM mail_participants WHERE user_id = %s", (int(user_id),))
    return cursor.fetchone() is not None


# ─── Справочники ─────────────────────────────────────────────────────────────

# Онлайн-офис («ОНЛАЙН · все города») — запись справочника без адреса: бумаги туда не
# отвезти, в списке «откуда/куда» ему не место.
_OFFICES_SQL = """
    SELECT o.id, o.city, o.name, o.address, o.phone
      FROM wiki_offices o
     WHERE o.status = 'active'
       AND o.kind = 'park'
       AND o.no_office = FALSE
       AND o.is_online = FALSE
       AND o.space_id = ANY(%(spaces)s::int[])
       AND COALESCE(NULLIF(btrim(o.city), ''), '') <> ''
     ORDER BY o.city, o.position, o.name
"""


def section_space_ids(cursor):
    from wiki import structure as wiki_structure

    return wiki_structure.space_ids_for_departments(cursor, OFFICE_DEPARTMENT_CODES)


def list_offices(cursor, space_ids=None):
    """Офисы для форм и «Участников»: те же, что в «Посылках» (наши офисы, действующие)."""
    if space_ids is None:
        space_ids = section_space_ids(cursor)
    space_ids = [int(x) for x in (space_ids or [])]
    if not space_ids:
        return []
    cursor.execute(_OFFICES_SQL, {'spaces': space_ids})
    return [{'id': row[0], 'city': row[1], 'name': row[2], 'address': row[3], 'phone': row[4]}
            for row in cursor.fetchall()]


def offices_by_id(cursor, office_ids, space_ids=None):
    """Офисы по id — в том числе убранные в архив: у участника офис мог уйти в архив, а подпись
    остаться нужна. Граница пространства — та же, что у списка офисов раздела (правило проекта:
    справочник вики вне вики читается только с пространством, tests/test_wiki_directory_space.py)."""
    ids = sorted({int(x) for x in office_ids if x})
    if space_ids is None:
        space_ids = section_space_ids(cursor)
    space_ids = [int(x) for x in (space_ids or [])]
    if not ids or not space_ids:
        return {}
    cursor.execute("SELECT id, city, name, address, phone, status FROM wiki_offices "
                   "WHERE id = ANY(%s::int[]) AND space_id = ANY(%s::int[])", (ids, space_ids))
    return {row[0]: {'id': row[0], 'city': row[1], 'name': row[2], 'address': row[3], 'phone': row[4],
                     'active': row[5] == 'active'} for row in cursor.fetchall()}


def list_legal_entities(cursor):
    cursor.execute("SELECT id, name FROM payment_legal_entities WHERE is_active ORDER BY lower(name), id")
    return [{'id': row[0], 'name': row[1]} for row in cursor.fetchall()]


def read_legal_entity(cursor, entity_id):
    cursor.execute("SELECT id, name, is_active FROM payment_legal_entities WHERE id = %s", (int(entity_id),))
    row = cursor.fetchone()
    return {'id': row[0], 'name': row[1], 'active': bool(row[2])} if row else None


# ─── Участники ───────────────────────────────────────────────────────────────

def list_participants(cursor):
    cursor.execute(
        """
        SELECT p.user_id, u.name, d.name, u.city, u.status, p.role,
               COALESCE((SELECT array_agg(po.office_id ORDER BY po.office_id)
                           FROM mail_participant_offices po WHERE po.user_id = p.user_id), '{}'),
               COALESCE(ub.name, cb.name), p.updated_at
          FROM mail_participants p
          JOIN users u ON u.id = p.user_id
          LEFT JOIN departments d ON d.id = u.department_id
          LEFT JOIN users cb ON cb.id = p.created_by
          LEFT JOIN users ub ON ub.id = p.updated_by
         ORDER BY lower(u.name), p.user_id
        """)
    return [{
        'user_id': row[0], 'name': row[1], 'department': row[2], 'city': row[3],
        'fired': row[4] == 'fired', 'role': row[5], 'office_ids': [int(x) for x in row[6] or []],
        'changed_by': row[7], 'changed_at': _iso(row[8]),
    } for row in cursor.fetchall()]


def read_user(cursor, user_id):
    cursor.execute("SELECT id, name, status FROM users WHERE id = %s", (int(user_id),))
    row = cursor.fetchone()
    return {'id': row[0], 'name': row[1], 'fired': row[2] == 'fired'} if row else None


def save_participant(cursor, user_id, role, office_ids, actor_id):
    cursor.execute(
        """
        INSERT INTO mail_participants (user_id, role, created_by, updated_by)
        VALUES (%(user)s, %(role)s, %(actor)s, %(actor)s)
        ON CONFLICT (user_id) DO UPDATE
           SET role = EXCLUDED.role, updated_by = EXCLUDED.updated_by, updated_at = """ + _NOW + """
        """, {'user': int(user_id), 'role': role, 'actor': actor_id})
    cursor.execute("DELETE FROM mail_participant_offices WHERE user_id = %s", (int(user_id),))
    for office_id in office_ids:
        cursor.execute("INSERT INTO mail_participant_offices (user_id, office_id) VALUES (%s, %s)",
                       (int(user_id), int(office_id)))


def delete_participant(cursor, user_id):
    cursor.execute("DELETE FROM mail_participants WHERE user_id = %s RETURNING user_id", (int(user_id),))
    return cursor.fetchone() is not None


def search_people(cursor, query, limit=20):
    """Кого добавить в «Участники»: действующие сотрудники, поиск по ФИО и логину."""
    words = [word for word in str(query or '').strip().split() if word][:4]
    params = {'limit': int(limit)}
    clauses = ["COALESCE(u.status, '') <> 'fired'"]
    for index, word in enumerate(words):
        key = 'w%d' % index
        params[key] = '%' + word.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_') + '%'
        clauses.append("(u.name ILIKE %(" + key + ")s OR u.login ILIKE %(" + key + ")s)")
    cursor.execute(
        """
        SELECT u.id, u.name, d.name, u.city, p.role
          FROM users u
          LEFT JOIN departments d ON d.id = u.department_id
          LEFT JOIN mail_participants p ON p.user_id = u.id
         WHERE """ + ' AND '.join(clauses) + """
         ORDER BY lower(u.name), u.id
         LIMIT %(limit)s
        """, params)
    return [{'id': row[0], 'name': row[1], 'department': row[2], 'city': row[3], 'role': row[4]}
            for row in cursor.fetchall()]


# ─── Настройки: зоны и маршруты ──────────────────────────────────────────────

def load_zones(cursor):
    cursor.execute(
        "SELECT code, label, min_days, max_days, eco_min_days, eco_max_days, position "
        "FROM mail_zones ORDER BY position, code")
    return [{'code': row[0], 'label': row[1], 'min_days': row[2], 'max_days': row[3],
             'eco_min_days': row[4], 'eco_max_days': row[5], 'position': row[6]} for row in cursor.fetchall()]


def upsert_zones(cursor, zones):
    """Новые и изменённые зоны. Лишние убирает drop_zones_except — ПОСЛЕ замены маршрутов:
    зону и ссылки на неё меняют одним сохранением, и порядок решает, пройдёт ли оно."""
    for zone in zones:
        cursor.execute(
            """
            INSERT INTO mail_zones (code, label, min_days, max_days, eco_min_days, eco_max_days, position)
            VALUES (%(code)s, %(label)s, %(min_days)s, %(max_days)s, %(eco_min_days)s, %(eco_max_days)s,
                    %(position)s)
            ON CONFLICT (code) DO UPDATE
               SET label = EXCLUDED.label, min_days = EXCLUDED.min_days, max_days = EXCLUDED.max_days,
                   eco_min_days = EXCLUDED.eco_min_days, eco_max_days = EXCLUDED.eco_max_days,
                   position = EXCLUDED.position
            """, zone)


def drop_zones_except(cursor, codes):
    """Убирает зоны, которых нет в новом справочнике. На зону ещё ссылаются маршруты —
    отказ: пара городов иначе молча осталась бы без срока."""
    cursor.execute(
        "SELECT DISTINCT zone_code FROM mail_routes WHERE NOT (zone_code = ANY(%s::varchar[]))", (list(codes),))
    used = [row[0] for row in cursor.fetchall()]
    if used:
        raise rules.MailError('Зона %s используется в маршрутах — сначала поменяйте маршруты' % ', '.join(used),
                              code='MAIL_ZONE_IN_USE', status=409)
    cursor.execute("DELETE FROM mail_zones WHERE NOT (code = ANY(%s::varchar[]))", (list(codes),))


def load_routes(cursor):
    cursor.execute("SELECT from_city, to_city, zone_code FROM mail_routes ORDER BY lower(from_city), lower(to_city)")
    return [{'from_city': row[0], 'to_city': row[1], 'zone_code': row[2]} for row in cursor.fetchall()]


def routes_map(cursor):
    return {(rules.normalize_city(item['from_city']), rules.normalize_city(item['to_city'])): item['zone_code']
            for item in load_routes(cursor)}


def save_routes(cursor, routes, actor_id):
    cursor.execute("DELETE FROM mail_routes")
    for route in routes:
        cursor.execute(
            "INSERT INTO mail_routes (from_city, to_city, zone_code, updated_by) VALUES (%s, %s, %s, %s)",
            (route['from_city'], route['to_city'], route['zone_code'], actor_id))


def zone_by_code(cursor, code):
    for zone in load_zones(cursor):
        if zone['code'] == code:
            return zone
    return None


# ─── Отправления: чтение ─────────────────────────────────────────────────────

# Список колонок ОДИН: и SELECT, и разбор строки собираются из него (урок «Посылок»:
# колонка, дописанная в середину SELECT при разборе по номерам, уронила прод).
_SHIPMENT_FIELDS = (
    'id', 'number', 'status', 'status_changed_at', 'status_changed_by', 'created_by', 'created_by_name',
    'created_at', 'updated_at',
    'sender_office_id', 'sender_city', 'sender_office_name', 'sender_office_address', 'sender_name',
    'sender_phone',
    'recipient_office_id', 'recipient_city', 'recipient_office_name', 'recipient_name', 'recipient_phone',
    'recipient_address', 'recipient_postcode',
    'payer_entity_id', 'payer_name', 'delivery_type', 'urgency', 'places', 'weight_kg', 'volume_weight_kg',
    'declared_value', 'comment',
    'ready_at', 'courier_requested_at', 'planned_pickup_on', 'track_number', 'handed_at', 'zone_code',
    'zone_manual', 'sla_min_days', 'sla_max_days', 'earliest_on', 'due_on', 'delivered_on',
    'reconciled_at', 'reconciled_by', 'reconciled_by_name', 'closed_at',
)
_NUMERIC_FIELDS = frozenset({'weight_kg', 'volume_weight_kg', 'declared_value'})
# Вычисляемые поля списка: краткое содержимое, итоги заявлено/получено, конфиденциальность.
_EXTRA_FIELDS = ('items_brief', 'is_confidential', 'status_changed_by_name')
_EXTRA_SQL = """
    (SELECT json_agg(json_build_object(
                'doc_type', i.doc_type, 'other_description', i.other_description, 'qty', i.qty,
                'received_qty', i.received_qty, 'line_status', i.line_status,
                'period_from', i.period_from, 'period_to', i.period_to) ORDER BY i.position, i.id)
       FROM mail_items i WHERE i.shipment_id = s.id),
    EXISTS (SELECT 1 FROM mail_items i WHERE i.shipment_id = s.id AND i.is_confidential),
    (SELECT su.name FROM users su WHERE su.id = s.status_changed_by)
"""
_COLUMNS = ', '.join('s.%s' % name for name in _SHIPMENT_FIELDS) + ',' + _EXTRA_SQL


def _shipment_row(row):
    data = {}
    for name, value in zip(_SHIPMENT_FIELDS + _EXTRA_FIELDS, row):
        if name in _NUMERIC_FIELDS:
            value = _num(value)
        data[name] = value
    brief = data.pop('items_brief') or []
    if isinstance(brief, str):
        brief = json.loads(brief)
    data['items_summary'] = rules.items_summary(brief)
    data['items_count'] = len(brief)
    data['qty_total'] = sum(int(item.get('qty') or 0) for item in brief)
    received = [item.get('received_qty') for item in brief]
    data['received_total'] = sum(int(x) for x in received) if brief and all(x is not None for x in received) \
        else None
    data['doc_types'] = sorted({item.get('doc_type') for item in brief if item.get('doc_type')})
    periods_from = [item['period_from'] for item in brief if item.get('period_from')]
    periods_to = [item['period_to'] for item in brief if item.get('period_to')]
    data['period_from'] = min(periods_from) if periods_from else None
    data['period_to'] = max(periods_to) if periods_to else None
    data['is_confidential'] = bool(data.get('is_confidential'))
    return data


def serialize_shipment(data, today, holidays):
    """Поля для JSON + состояние срока, посчитанное тем же правилом, что во фронте."""
    out = {key: _iso(value) for key, value in data.items()}
    due_on = data.get('due_on')
    out['deadline'] = rules.deadline_state(data.get('status'), due_on, today, holidays)
    out['overdue'] = rules.is_overdue(data.get('status'), due_on, today)
    return out


def _visibility_clause(ctx, params):
    """Та же видимость, что access.can_view, только в SQL."""
    if access.is_full(ctx):
        return 'TRUE'
    if ctx.get('role') == 'accounting':
        return "s.status <> 'draft'"
    params['vis_me'] = int(ctx['user_id'])
    params['vis_offices'] = sorted(access.own_offices(ctx))
    drafts = 'TRUE' if ctx.get('role') == 'coordinator' else "s.status <> 'draft'"
    return ("(s.created_by = %(vis_me)s"
            " OR (s.sender_office_id = ANY(%(vis_offices)s::int[]) AND " + drafts + ")"
            " OR (s.recipient_office_id = ANY(%(vis_offices)s::int[]) AND s.status IN ("
            + _in(rules.INCOMING_VISIBLE) + ")))")


PROBLEM_TAB_STATUSES = ('problem', 'not_delivered', 'return_initiated', 'returned')
SCOPES = ('outgoing', 'incoming', 'registry', 'problems')


def _scope_clause(scope, ctx, params):
    if scope in ('outgoing', 'incoming'):
        params['scope_offices'] = sorted(access.own_offices(ctx))
        if scope == 'outgoing':
            return "s.sender_office_id = ANY(%(scope_offices)s::int[])"
        return ("s.recipient_office_id = ANY(%(scope_offices)s::int[]) AND s.status IN ("
                + _in(rules.INCOMING_VISIBLE) + ")")
    if scope == 'problems':
        return ("(s.status IN (" + _in(PROBLEM_TAB_STATUSES) + ") OR (s.status IN ("
                + _in(rules.ON_THE_WAY) + ") AND s.due_on < %(today)s))")
    return 'TRUE'


def _quick_clause(quick):
    """Быстрые вкладки (ТЗ п. 9). Плитки не пересекаются: опоздавшее «В пути» — в «Просрочено»."""
    if quick == 'to_send':
        return "s.status IN ('ready', 'courier_requested')"
    if quick == 'in_transit':
        return ("s.status IN (" + _in(rules.ON_THE_WAY) + ") AND (s.due_on IS NULL OR s.due_on >= %(today)s)")
    if quick == 'to_reconcile':
        return "s.status = 'delivered'"
    if quick == 'overdue':
        return "s.status IN (" + _in(rules.ON_THE_WAY) + ") AND s.due_on < %(today)s"
    if quick == 'problems':
        return "s.status IN ('problem', 'not_delivered', 'return_initiated')"
    if quick == 'drafts':
        return "s.status = 'draft'"
    return 'TRUE'


QUICK_CODES = ('to_send', 'in_transit', 'to_reconcile', 'overdue', 'problems')


def _like(value):
    return '%' + str(value).replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_') + '%'


def _search_clause(query, params):
    """Поиск по ID, треку, городу, офису, ФИО, телефону и типу документа (ТЗ п. 9)."""
    words = [word for word in str(query or '').strip().split() if word][:5]
    parts = []
    for index, word in enumerate(words):
        key = 'q%d' % index
        params[key] = _like(word)
        doc_codes = [code for code, label in rules.DOC_TYPE_LABELS.items() if word.lower() in label.lower()]
        params[key + '_docs'] = doc_codes
        options = [
            "s.number ILIKE %(" + key + ")s", "s.track_number ILIKE %(" + key + ")s",
            "s.sender_city ILIKE %(" + key + ")s", "s.recipient_city ILIKE %(" + key + ")s",
            "s.sender_office_name ILIKE %(" + key + ")s", "s.recipient_office_name ILIKE %(" + key + ")s",
            "s.sender_name ILIKE %(" + key + ")s", "s.recipient_name ILIKE %(" + key + ")s",
            "s.created_by_name ILIKE %(" + key + ")s",
            "EXISTS (SELECT 1 FROM mail_items i WHERE i.shipment_id = s.id AND ("
            "i.doc_type = ANY(%(" + key + "_docs)s::varchar[]) OR i.other_description ILIKE %(" + key + ")s"
            " OR i.doc_number ILIKE %(" + key + ")s))",
        ]
        # Телефон диктуют как угодно («8 771 973-69-25»), в карточке он записан иначе —
        # сравниваем одни цифры, от четырёх подряд.
        digits = ''.join(ch for ch in word if ch.isdigit())
        if len(digits) >= 4:
            params[key + '_digits'] = '%' + digits + '%'
            options.append(
                "regexp_replace(COALESCE(s.recipient_phone, '') || ' ' || COALESCE(s.sender_phone, ''),"
                " '[^0-9]', '', 'g') LIKE %(" + key + "_digits)s")
        parts.append('(' + ' OR '.join(options) + ')')
    return ' AND '.join(parts) if parts else 'TRUE'


# Отбор по дате события (ТЗ п. 9: «фильтры по периоду документа и событиям отдельно»):
# код → (колонка, это дата или время).
EVENT_COLUMNS = {
    'created': ('s.created_at', False),
    'handed': ('s.handed_at', False),
    'delivered': ('s.delivered_on', True),
    'reconciled': ('s.reconciled_at', False),
}


def _filters(ctx, params, *, scope='registry', quick=None, query=None, status=None, office_id=None,
             event_kind='created', event_from=None, event_to=None, doc_from=None, doc_to=None):
    clauses = [_visibility_clause(ctx, params), _scope_clause(scope, ctx, params)]
    if quick:
        clauses.append(_quick_clause(quick))
    if status:
        params['status'] = status
        clauses.append('s.status = %(status)s')
    if office_id:
        params['office_id'] = int(office_id)
        clauses.append('(s.sender_office_id = %(office_id)s OR s.recipient_office_id = %(office_id)s)')
    column, is_date = EVENT_COLUMNS.get(event_kind or 'created', EVENT_COLUMNS['created'])
    if event_from:
        params['event_from'] = event_from
        clauses.append('%s >= %%(event_from)s' % column)
    if event_to:
        if is_date:
            params['event_to'] = event_to
            clauses.append('%s <= %%(event_to)s' % column)
        else:
            params['event_to'] = event_to + timedelta(days=1)
            clauses.append('%s < %%(event_to)s' % column)
    # Период ДОКУМЕНТА — отдельно от дат событий (ТЗ п. 9): строка, чей период
    # пересекается с выбранным.
    if doc_from or doc_to:
        params['doc_from'] = doc_from or date(1900, 1, 1)
        params['doc_to'] = doc_to or date(2999, 12, 31)
        clauses.append(
            "EXISTS (SELECT 1 FROM mail_items i WHERE i.shipment_id = s.id"
            " AND COALESCE(i.period_to, i.period_from) >= %(doc_from)s"
            " AND COALESCE(i.period_from, i.period_to) <= %(doc_to)s)")
    if query:
        clauses.append(_search_clause(query, params))
    return ' AND '.join('(%s)' % clause for clause in clauses)


def list_shipments(cursor, ctx, *, today, limit=50, offset=0, **filters):
    params = {'today': today, 'limit': int(limit), 'offset': int(offset)}
    where = _filters(ctx, params, **filters)
    cursor.execute(
        "SELECT " + _COLUMNS + ", COUNT(*) OVER () FROM mail_shipments s WHERE " + where
        + " ORDER BY s.created_at DESC, s.id DESC LIMIT %(limit)s OFFSET %(offset)s", params)
    rows = cursor.fetchall()
    total = int(rows[0][-1]) if rows else 0
    if not rows and int(offset) > 0:
        count_params = {'today': today}
        cursor.execute("SELECT COUNT(*) FROM mail_shipments s WHERE "
                       + _filters(ctx, count_params, **filters), count_params)
        total = int(cursor.fetchone()[0] or 0)
    return [_shipment_row(row[:-1]) for row in rows], total


def counters(cursor, ctx, *, today, scope='registry', **filters):
    """Числа быстрых вкладок в той же области и с тем же поиском, что и список."""
    filters = dict(filters)
    filters.pop('quick', None)
    params = {'today': today}
    where = _filters(ctx, params, scope=scope, **filters)
    selects = ', '.join('COUNT(*) FILTER (WHERE %s)' % _quick_clause(code) for code in QUICK_CODES)
    cursor.execute("SELECT COUNT(*), " + selects + " FROM mail_shipments s WHERE " + where, params)
    row = cursor.fetchone() or (0,) * (len(QUICK_CODES) + 1)
    result = {'all': int(row[0] or 0)}
    for code, value in zip(QUICK_CODES, row[1:]):
        result[code] = int(value or 0)
    return result


def attention(cursor, ctx, *, today, limit=10):
    """«Требуют внимания» на «Обзоре»: что ждёт именно этого человека.

    сверить     входящие в его офисы, доставленные курьером (своё не принимают)
    отправить   «Готово к отправке» и «Заявка курьеру» из его офисов
    черновики   его собственные
    просрочено  в его области видимости
    проблемы    в его области видимости
    """
    own = sorted(access.own_offices(ctx))
    acting = ctx.get('role') in access.ACTING_ROLES or access.is_full(ctx)
    groups = []

    def fetch(reason, extra_sql, extra_params):
        params = {'today': today, 'limit': int(limit)}
        params.update(extra_params)
        where = _visibility_clause(ctx, params) + ' AND ' + extra_sql
        cursor.execute(
            "SELECT " + _COLUMNS + ", COUNT(*) OVER () FROM mail_shipments s WHERE " + where
            + " ORDER BY s.due_on NULLS LAST, s.status_changed_at, s.id LIMIT %(limit)s", params)
        rows = cursor.fetchall()
        if rows:
            groups.append({'reason': reason, 'total': int(rows[0][-1]),
                           'items': [_shipment_row(row[:-1]) for row in rows]})

    if acting and own:
        fetch('to_reconcile', "s.status = 'delivered' AND s.recipient_office_id = ANY(%(own)s::int[])"
                              " AND s.created_by IS DISTINCT FROM %(me)s",
              {'own': own, 'me': ctx['user_id']})
        fetch('to_send', "s.status IN ('ready', 'courier_requested') AND s.sender_office_id = ANY(%(own)s::int[])",
              {'own': own})
    if acting:
        fetch('drafts', "s.status = 'draft' AND s.created_by = %(me)s", {'me': ctx['user_id']})
    fetch('overdue', _quick_clause('overdue'), {})
    fetch('problems', _quick_clause('problems'), {})
    return groups


def read_shipment(cursor, shipment_id, *, for_update=False):
    cursor.execute("SELECT " + _COLUMNS + " FROM mail_shipments s WHERE s.id = %s"
                   + (" FOR UPDATE OF s" if for_update else ""), (int(shipment_id),))
    row = cursor.fetchone()
    return _shipment_row(row) if row else None


_ITEM_FIELDS = ('id', 'position', 'doc_type', 'other_description', 'period_from', 'period_to', 'qty',
                'copy_kind', 'doc_number', 'is_confidential', 'received_qty', 'line_status', 'line_note')


def read_items(cursor, shipment_id):
    cursor.execute("SELECT " + ', '.join(_ITEM_FIELDS) + " FROM mail_items WHERE shipment_id = %s "
                   "ORDER BY position, id", (int(shipment_id),))
    return [dict(zip(_ITEM_FIELDS, row)) for row in cursor.fetchall()]


def serialize_items(items):
    return [{key: _iso(value) for key, value in item.items()} for item in items]


def read_events(cursor, shipment_id):
    cursor.execute(
        "SELECT id, kind, from_status, to_status, actor_id, actor_name, source, note, payload, at "
        "FROM mail_events WHERE shipment_id = %s ORDER BY at, id", (int(shipment_id),))
    events = []
    for row in cursor.fetchall():
        payload = row[8]
        if isinstance(payload, str):
            payload = json.loads(payload)
        events.append({'id': row[0], 'kind': row[1], 'from_status': row[2], 'to_status': row[3],
                       'actor_id': row[4], 'actor_name': row[5], 'source': row[6], 'note': row[7],
                       'payload': payload or {}, 'at': _iso(row[9])})
    return events


def redact_events(events):
    """История без имён файлов — тому, кому вложения отправления закрыты (бухгалтерия при
    конфиденциальных строках, ТЗ п. 11): имя файла часто само говорит, что внутри. Вид файла
    остаётся — что подтверждение было, видно."""
    redacted = []
    for event in events:
        payload = dict(event.get('payload') or {})
        if payload.get('filename'):
            payload['filename'] = None
        if payload.get('files'):
            payload['files'] = [{'kind': item.get('kind'), 'filename': None} for item in payload['files']]
        redacted.append(dict(event, payload=payload))
    return redacted


def read_files(cursor, shipment_id):
    cursor.execute(
        "SELECT id, event_id, kind, filename, content_type, size_bytes, uploaded_by_name, uploaded_at "
        "FROM mail_files WHERE shipment_id = %s ORDER BY id", (int(shipment_id),))
    return [{'id': row[0], 'event_id': row[1], 'kind': row[2], 'filename': row[3], 'content_type': row[4],
             'size': row[5], 'uploaded_by_name': row[6], 'uploaded_at': _iso(row[7])} for row in cursor.fetchall()]


def read_file(cursor, file_id):
    cursor.execute("SELECT id, shipment_id, filename, content_type, bucket, blob_path FROM mail_files WHERE id = %s",
                   (int(file_id),))
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': row[0], 'shipment_id': row[1], 'filename': row[2], 'content_type': row[3],
            'bucket': row[4], 'blob_path': row[5]}


def has_proof_file(cursor, shipment_id):
    """Есть ли у отправления накладная или подтверждение передачи (ТЗ п. 14)."""
    cursor.execute("SELECT 1 FROM mail_files WHERE shipment_id = %s AND kind IN ('waybill', 'handover') LIMIT 1",
                   (int(shipment_id),))
    return cursor.fetchone() is not None


def track_duplicates(cursor, track_number, exclude_id=None):
    """Другие отправления с тем же треком (у раздела один курьер — MBE KZ)."""
    cursor.execute(
        "SELECT number FROM mail_shipments WHERE lower(track_number) = lower(%s) AND id <> %s "
        "AND status <> 'cancelled' ORDER BY id LIMIT 5",
        (str(track_number).strip(), int(exclude_id or 0)))
    return [row[0] for row in cursor.fetchall()]


# ─── Отправления: запись ─────────────────────────────────────────────────────

def next_number(cursor, year):
    cursor.execute(
        """
        INSERT INTO mail_counters (year, last_value) VALUES (%s, 1)
        ON CONFLICT (year) DO UPDATE SET last_value = mail_counters.last_value + 1
        RETURNING last_value
        """, (int(year),))
    return rules.format_number(year, cursor.fetchone()[0])


def log_event(cursor, shipment_id, kind, actor, *, from_status=None, to_status=None, note=None, payload=None):
    cursor.execute(
        """
        INSERT INTO mail_events (shipment_id, kind, from_status, to_status, actor_id, actor_name, note, payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        RETURNING id
        """, (int(shipment_id), kind, from_status, to_status, actor['user_id'], actor.get('name'), note,
              json.dumps(payload or {}, ensure_ascii=False, default=_iso)))
    return cursor.fetchone()[0]


def _snapshot_fields(fields, offices, payer):
    """Копии офисов и плательщика в карточку — рядом с id."""
    sender = offices.get(fields.get('sender_office_id')) or {}
    recipient = offices.get(fields.get('recipient_office_id')) or {}
    snapshot = dict(fields)
    snapshot.update({
        'sender_city': sender.get('city'),
        'sender_office_name': sender.get('name'),
        'sender_office_address': sender.get('address'),
        'recipient_city': recipient.get('city'),
        'recipient_office_name': recipient.get('name'),
        'payer_name': (payer or {}).get('name'),
    })
    return snapshot


_WRITE_FIELDS = rules.SHIPMENT_FIELDS + ('sender_city', 'sender_office_name', 'sender_office_address',
                                         'recipient_city', 'recipient_office_name', 'payer_name')


def _replace_items(cursor, shipment_id, items):
    cursor.execute("DELETE FROM mail_items WHERE shipment_id = %s", (int(shipment_id),))
    for position, item in enumerate(items, start=1):
        cursor.execute(
            """
            INSERT INTO mail_items (shipment_id, position, doc_type, other_description, period_from, period_to,
                                    qty, copy_kind, doc_number, is_confidential)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (int(shipment_id), position, item['doc_type'], item['other_description'], item['period_from'],
                  item['period_to'], item['qty'], item['copy_kind'], item['doc_number'], item['is_confidential']))


def create_shipment(cursor, *, fields, items, offices, payer, actor, ready, year):
    number = next_number(cursor, year)
    data = _snapshot_fields(fields, offices, payer)
    columns = list(_WRITE_FIELDS)
    values = [data.get(name) for name in columns]
    status = 'ready' if ready else 'draft'
    cursor.execute(
        "INSERT INTO mail_shipments (number, status, status_changed_by, created_by, created_by_name, ready_at, "
        + ', '.join(columns) + ") VALUES (%s, %s, %s, %s, %s, " + ("%s" % _NOW if ready else 'NULL') + ", "
        + ', '.join(['%s'] * len(columns)) + ") RETURNING id",
        [number, status, actor['user_id'], actor['user_id'], actor.get('name')] + values)
    shipment_id = cursor.fetchone()[0]
    _replace_items(cursor, shipment_id, items)
    log_event(cursor, shipment_id, 'created', actor, to_status=status,
              payload={'items': len(items)})
    return shipment_id


def update_shipment(cursor, shipment_id, *, fields, items, offices, payer, actor, changes, items_changed,
                    reason=None, extra_payload=None):
    data = _snapshot_fields(fields, offices, payer)
    columns = list(_WRITE_FIELDS)
    cursor.execute(
        "UPDATE mail_shipments SET " + ', '.join('%s = %%s' % name for name in columns)
        + ", updated_at = " + _NOW + " WHERE id = %s",
        [data.get(name) for name in columns] + [int(shipment_id)])
    if items is not None and items_changed:
        _replace_items(cursor, shipment_id, items)
    payload = {'changes': changes}
    if items_changed:
        payload['items_changed'] = True
    payload.update(extra_payload or {})
    log_event(cursor, shipment_id, 'edited', actor, note=reason, payload=payload)


# «Записать текущее время по Алматы» в поле шага. Отдельный объект, а не строка:
# трек-номер, введённый человеком, совпасть с ним не может.
NOW_VALUE = object()


def set_status(cursor, shipment_id, *, status, actor, extra=None):
    """Новый статус и поля шага одним UPDATE. extra — {колонка: значение} из белого списка."""
    allowed = {'ready_at', 'courier_requested_at', 'planned_pickup_on', 'track_number', 'handed_at', 'zone_code',
               'zone_manual', 'sla_min_days', 'sla_max_days', 'earliest_on', 'due_on', 'delivered_on',
               'reconciled_at', 'reconciled_by', 'reconciled_by_name'}
    extra = dict(extra or {})
    unknown = set(extra) - allowed
    if unknown:
        raise ValueError('mail.set_status: неизвестные поля %s' % sorted(unknown))
    sets = ['status = %(status)s', 'status_changed_by = %(actor)s', 'status_changed_at = ' + _NOW,
            'updated_at = ' + _NOW]
    params = {'status': status, 'actor': actor['user_id'], 'id': int(shipment_id)}
    for name, value in extra.items():
        if value is NOW_VALUE:
            sets.append('%s = %s' % (name, _NOW))
        else:
            sets.append('%s = %%(%s)s' % (name, name))
            params[name] = value
    if status in rules.FINAL:
        sets.append('closed_at = ' + _NOW)
    cursor.execute("UPDATE mail_shipments SET " + ', '.join(sets) + " WHERE id = %(id)s", params)


def update_fields(cursor, shipment_id, values):
    """Поля шага без смены статуса: «Забор не состоялся» — новая дата забора; правка передачи
    курьеру (трек, время) — со сроком, пересчитанным от нового времени; смена типа доставки
    после передачи — с пересчитанным сроком."""
    allowed = {'planned_pickup_on', 'track_number', 'handed_at', 'sla_min_days', 'sla_max_days', 'earliest_on',
               'due_on', 'zone_code', 'zone_manual'}
    unknown = set(values) - allowed
    if unknown:
        raise ValueError('mail.update_fields: неизвестные поля %s' % sorted(unknown))
    sets = ['%s = %%(%s)s' % (name, name) for name in values] + ['updated_at = ' + _NOW]
    params = dict(values)
    params['id'] = int(shipment_id)
    cursor.execute("UPDATE mail_shipments SET " + ', '.join(sets) + " WHERE id = %(id)s", params)


def save_reconciliation(cursor, lines):
    for line in lines:
        cursor.execute(
            "UPDATE mail_items SET received_qty = %s, line_status = %s, line_note = %s WHERE id = %s",
            (line['received_qty'], line['line_status'], line['line_note'], int(line['id'])))


def insert_file(cursor, shipment_id, *, kind, filename, content_type, size, bucket, blob_path, actor,
                event_id=None):
    cursor.execute(
        """
        INSERT INTO mail_files (shipment_id, event_id, kind, filename, content_type, size_bytes, bucket, blob_path,
                                uploaded_by, uploaded_by_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """, (int(shipment_id), event_id, kind, filename, content_type, int(size), bucket, blob_path,
              actor['user_id'], actor.get('name')))
    return cursor.fetchone()[0]


# ─── Выгрузка ────────────────────────────────────────────────────────────────

EXPORT_LIMIT = 5000


def export_data(cursor, ctx, *, today, **filters):
    """Отправления по фильтрам реестра + их строки и история (ТЗ п. 13: реестр, отдельный
    лист построчного содержимого, история событий)."""
    rows, total = list_shipments(cursor, ctx, today=today, limit=EXPORT_LIMIT, offset=0, **filters)
    ids = [row['id'] for row in rows]
    items, events = {}, {}
    if ids:
        cursor.execute("SELECT shipment_id, " + ', '.join(_ITEM_FIELDS) + " FROM mail_items "
                       "WHERE shipment_id = ANY(%s::int[]) ORDER BY shipment_id, position, id", (ids,))
        for row in cursor.fetchall():
            items.setdefault(row[0], []).append(dict(zip(_ITEM_FIELDS, row[1:])))
        cursor.execute(
            "SELECT shipment_id, kind, from_status, to_status, actor_name, note, payload, at FROM mail_events "
            "WHERE shipment_id = ANY(%s::int[]) ORDER BY shipment_id, at, id", (ids,))
        for row in cursor.fetchall():
            payload = row[6]
            if isinstance(payload, str):
                payload = json.loads(payload)
            events.setdefault(row[0], []).append({
                'kind': row[1], 'from_status': row[2], 'to_status': row[3], 'actor_name': row[4],
                'note': row[5], 'payload': payload or {}, 'at': row[7]})
    return rows, items, events, total
