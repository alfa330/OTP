"""SQL-слой раздела «Основные средства».

Функции принимают ГОТОВЫЙ курсор и не управляют транзакцией — её держит вызывающий
(routes.py). Смена статуса запроса, правка карточки ОС и строка истории идут одним
курсором: статус без следа «кто и когда» остаться не может.

Видимость (asset_scope, request_scope) — та же, что access.asset_level и
access.can_view_request; правило меняется в обоих местах, расхождение ловит
tests/test_fixed_assets_postgres.py.
"""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from . import access, rules

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"
_ALMATY_OFFSET = timedelta(hours=5)
_FIRED = ('fired', 'dismissal')


def now_almaty():
    """Render живёт в UTC; у Казахстана с 01.03.2024 одна зона без перевода часов."""
    return datetime.utcnow() + _ALMATY_OFFSET


def today_almaty():
    return now_almaty().date()


def plain(value):
    """Значение для jsonify: Decimal → число, даты → isoformat без «GMT»."""
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [plain(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _like(text):
    """Подстрока для ILIKE: «%» и «_» из запроса ищутся буквально."""
    escaped = str(text or '').replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return '%' + escaped + '%'


def _cols(alias, fields):
    return ', '.join('%s.%s' % (alias, field) for field in fields)


def _json(value):
    return json.dumps(plain(value or {}), ensure_ascii=False)


# ─── Контекст зрителя ────────────────────────────────────────────────────────

def load_context(cursor, user_id):
    cursor.execute(
        """
        SELECT u.id, u.name, u.role, u.status, u.department_id, d.code, d.name, u.job_title,
               COALESCE((SELECT array_agg(h.id ORDER BY h.id) FROM departments h
                          WHERE h.head_user_id = u.id AND h.is_active), '{}'),
               COALESCE((SELECT array_agg(h.code ORDER BY h.id) FROM departments h
                          WHERE h.head_user_id = u.id AND h.is_active), '{}'),
               COALESCE((SELECT array_agg(m.role ORDER BY m.role) FROM fa_members m
                          WHERE m.user_id = u.id), '{}'),
               EXISTS (SELECT 1 FROM fa_assets a WHERE a.mol_user_id = u.id AND a.status = 'active'),
               EXISTS (SELECT 1 FROM fa_requests r
                        WHERE r.status <> 'draft'
                          AND u.id IN (r.created_by, r.from_user_id, r.to_user_id, r.need_for_user_id))
          FROM users u
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE u.id = %s
        """, (int(user_id),))
    row = cursor.fetchone()
    if not row:
        return None
    (_id, name, portal_role, status, department_id, department_code, department_name, job_title,
     headed_ids, headed_codes, member_roles, has_assets, involved) = row
    if str(status or '').strip().lower() == 'fired':
        return None
    portal_role = str(portal_role or '').strip().lower()
    return {
        'user_id': int(user_id),
        'name': name,
        'portal_role': portal_role,
        'is_super': portal_role == 'super_admin',
        'department_id': department_id,
        'department_code': department_code,
        'department_name': department_name,
        'job_title': job_title,
        'headed_department_ids': [int(x) for x in (headed_ids or [])],
        'headed_department_codes': [str(x) for x in (headed_codes or [])],
        'member_roles': [role for role in (member_roles or []) if role in rules.MEMBER_ROLES],
        'has_assets': bool(has_assets),
        'involved': bool(involved),
    }


def section_open_for(cursor, user_id):
    """Флаг профиля fixed_assets_access — то же правило, что у ручек раздела (access.can_open)."""
    return access.can_open(load_context(cursor, user_id))


# ─── Люди и отделы ───────────────────────────────────────────────────────────

def user_brief(cursor, user_id):
    if not user_id:
        return None
    cursor.execute(
        """
        SELECT u.id, u.name, u.status, u.department_id, d.name, u.job_title
          FROM users u LEFT JOIN departments d ON d.id = u.department_id
         WHERE u.id = %s
        """, (int(user_id),))
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': row[0], 'name': row[1], 'fired': str(row[2] or '').lower() in _FIRED,
            'department_id': row[3], 'department_name': row[4], 'job_title': row[5]}


def list_people(cursor):
    """Работающие сотрудники — для выбора МОЛ, получателя, «кто передал оригинал»."""
    cursor.execute(
        """
        SELECT u.id, u.name, u.department_id, d.name, u.job_title
          FROM users u LEFT JOIN departments d ON d.id = u.department_id
         WHERE COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')
           AND COALESCE(NULLIF(btrim(u.name), ''), '') <> ''
         ORDER BY lower(u.name), u.id
        """)
    return [{'id': row[0], 'name': row[1], 'department_id': row[2], 'department_name': row[3],
             'job_title': row[4]} for row in cursor.fetchall()]


def list_departments(cursor):
    cursor.execute("SELECT id, name, code, head_user_id FROM departments WHERE is_active ORDER BY lower(name), id")
    return [{'id': row[0], 'name': row[1], 'code': row[2], 'head_user_id': row[3]} for row in cursor.fetchall()]


def department_brief(cursor, department_id):
    if not department_id:
        return None
    cursor.execute("SELECT id, name, head_user_id FROM departments WHERE id = %s AND is_active", (int(department_id),))
    row = cursor.fetchone()
    return {'id': row[0], 'name': row[1], 'head_user_id': row[2]} if row else None


def department_heads(cursor, department_ids):
    """{id отдела: ФИО главы} — для подписей акта и выбора проверяющего."""
    ids = sorted({int(value) for value in department_ids or () if value})
    if not ids:
        return {}
    cursor.execute(
        """
        SELECT d.id, u.name
          FROM departments d JOIN users u ON u.id = d.head_user_id
         WHERE d.id = ANY(%s::int[]) AND d.is_active
           AND COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')
        """, (ids,))
    return {row[0]: row[1] for row in cursor.fetchall()}


# ─── Категории ───────────────────────────────────────────────────────────────

def list_categories(cursor, *, include_inactive=False):
    cursor.execute(
        "SELECT c.id, c.name, c.check_by, c.is_active, "
        "       (SELECT COUNT(*) FROM fa_assets a WHERE a.category_id = c.id) "
        "  FROM fa_categories c %s ORDER BY c.position, c.id" % ('' if include_inactive else 'WHERE c.is_active'))
    return [{'id': row[0], 'name': row[1], 'check_by': row[2], 'is_active': row[3], 'assets': int(row[4] or 0)}
            for row in cursor.fetchall()]


def read_category(cursor, category_id):
    cursor.execute("SELECT id, name, check_by, is_active FROM fa_categories WHERE id = %s", (int(category_id),))
    row = cursor.fetchone()
    return {'id': row[0], 'name': row[1], 'check_by': row[2], 'is_active': row[3]} if row else None


def category_name_taken(cursor, name, *, exclude_id=None):
    cursor.execute("SELECT 1 FROM fa_categories WHERE lower(name) = lower(%s) AND id <> %s",
                   (name, int(exclude_id or 0)))
    return cursor.fetchone() is not None


def create_category(cursor, name, check_by):
    cursor.execute(
        "INSERT INTO fa_categories (name, check_by, position) "
        "VALUES (%s, %s, COALESCE((SELECT MAX(position) FROM fa_categories), 0) + 1) RETURNING id",
        (name, check_by))
    return cursor.fetchone()[0]


def update_category(cursor, category_id, *, name, check_by, is_active):
    cursor.execute("UPDATE fa_categories SET name = %s, check_by = %s, is_active = %s WHERE id = %s",
                   (name, check_by, bool(is_active), int(category_id)))


# ─── «Участники» ─────────────────────────────────────────────────────────────

def list_members(cursor):
    cursor.execute(
        """
        SELECT m.user_id, u.name, d.name, m.role
          FROM fa_members m
          JOIN users u ON u.id = m.user_id
          LEFT JOIN departments d ON d.id = u.department_id
         ORDER BY m.role, lower(u.name)
        """)
    return [{'user_id': row[0], 'name': row[1], 'department_name': row[2], 'role': row[3]}
            for row in cursor.fetchall()]


def add_member(cursor, user_id, role, actor_id):
    cursor.execute(
        "INSERT INTO fa_members (user_id, role, created_by) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (int(user_id), role, actor_id))


def remove_member(cursor, user_id, role):
    cursor.execute("DELETE FROM fa_members WHERE user_id = %s AND role = %s", (int(user_id), role))


def role_departments(cursor):
    """Кто получает роли раздела по отделам — для справки во вкладке «Настройки»."""
    codes = {
        rules.MEMBER_ACCOUNTING: access.ACCOUNTING_DEPARTMENT_CODE,
        rules.MEMBER_HR: access.HR_DEPARTMENT_CODE,
        rules.MEMBER_IT: access.IT_DEPARTMENT_CODE,
        rules.MEMBER_OBSERVER: access.ANALYTICS_DEPARTMENT_CODE,
    }
    cursor.execute(
        """
        SELECT d.code, d.name, hu.name,
               (SELECT COUNT(*) FROM users u
                 WHERE u.department_id = d.id AND COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal'))
          FROM departments d
          LEFT JOIN users hu ON hu.id = d.head_user_id
         WHERE d.code = ANY(%s) AND d.is_active
        """, (list(codes.values()),))
    by_code = {row[0]: {'department': row[1], 'head': row[2], 'people': int(row[3] or 0)} for row in cursor.fetchall()}
    return {role: by_code.get(code) for role, code in codes.items()}


# ─── Настройки раздела ───────────────────────────────────────────────────────

REQUISITES_KEY = 'requisites'


def read_setting(cursor, key):
    cursor.execute("SELECT value, updated_by_name, updated_at FROM fa_settings WHERE key = %s", (key,))
    row = cursor.fetchone()
    if not row:
        return None
    value = row[0]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    return {'value': value if isinstance(value, dict) else {}, 'updated_by_name': row[1], 'updated_at': row[2]}


def write_setting(cursor, key, value, actor):
    cursor.execute(
        "INSERT INTO fa_settings (key, value, updated_by, updated_by_name) VALUES (%s, %s::jsonb, %s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, "
        "updated_by_name = EXCLUDED.updated_by_name, updated_at = " + _NOW,
        (key, _json(value), (actor or {}).get('id'), (actor or {}).get('name')))


def read_requisites(cursor):
    """Реквизиты организации и комиссия для печатных форм: {} пока бухгалтерия их не вписала."""
    row = read_setting(cursor, REQUISITES_KEY)
    return dict(row['value']) if row else {}


# ─── Основные средства ───────────────────────────────────────────────────────

ASSET_FIELDS = (
    'id', 'inventory_number', 'name', 'category_id', 'serial_number', 'barcode', 'cost', 'acquired_on', 'note', 'status',
    'mol_user_id', 'mol_name', 'department_id', 'department_name', 'location', 'on_stock',
    'open_request_id', 'intake_request_id', 'written_off_at', 'created_by', 'created_by_name',
    'created_at', 'updated_at',
)

_ASSET_SELECT = """
    SELECT %s, c.name, c.check_by, orq.kind, orq.status, irq.status,
           ph.id, ph.bucket, ph.blob_path, ph.thumb_blob_path, ph.content_type
      FROM fa_assets a
      JOIN fa_categories c ON c.id = a.category_id
      LEFT JOIN fa_requests orq ON orq.id = a.open_request_id
      LEFT JOIN fa_requests irq ON irq.id = a.intake_request_id
      LEFT JOIN LATERAL (
          SELECT f.id, f.bucket, f.blob_path, f.thumb_blob_path, f.content_type
            FROM fa_files f
            JOIN fa_requests fr ON fr.id = f.request_id
           WHERE f.asset_id = a.id AND f.kind = 'photo_whole' AND fr.status <> 'draft'
           ORDER BY f.id DESC
           LIMIT 1) ph ON TRUE
""" % _cols('a', ASSET_FIELDS)


def _asset_row(row):
    size = len(ASSET_FIELDS)
    asset = dict(zip(ASSET_FIELDS, row[:size]))
    (category_name, check_by, open_kind, open_status, intake_status,
     photo_id, bucket, blob_path, thumb_blob_path, content_type) = row[size:size + 10]
    asset['category_name'] = category_name
    asset['check_by'] = check_by
    asset['status_label'] = rules.ASSET_STATUS_LABELS.get(asset['status'], asset['status'])
    asset['open_request'] = ({'id': asset['open_request_id'], 'kind': open_kind, 'status': open_status,
                              'kind_label': rules.KIND_LABELS.get(open_kind, open_kind)}
                             if asset['open_request_id'] and open_kind else None)
    asset['intake_open'] = intake_status in rules.OPEN_STATUSES
    asset['cover'] = ({'id': photo_id, 'bucket': bucket, 'blob_path': blob_path,
                       'thumb_blob_path': thumb_blob_path, 'content_type': content_type}
                      if photo_id else None)
    return asset


def asset_scope(ctx):
    """Какие ОС зритель видит в реестре: (условие SQL, параметры). Двойник access.asset_level."""
    if access.sees_registry(ctx):
        return 'TRUE', {}
    return ('(a.mol_user_id = %(viewer)s OR a.department_id = ANY(%(headed)s::int[]))',
            {'viewer': ctx['user_id'], 'headed': sorted(access.headed_ids(ctx))})


# Срезы реестра (плитки над списком).
ASSET_SLICES = {
    'in_use': "a.status = 'active' AND NOT a.on_stock",
    'stock': "a.status = 'active' AND a.on_stock",
    'busy': "a.status = 'active' AND a.open_request_id IS NOT NULL",
    'written_off': "a.status = 'written_off'",
}


def _asset_filters(ctx, *, mine=False, free=False, query=None, category_id=None, department_id=None,
                   mol_user_id=None):
    scope_sql, params = asset_scope(ctx)
    clauses = [scope_sql]
    if free:
        clauses.append("a.status = 'active' AND a.open_request_id IS NULL")
    if mine:
        params['mine'] = ctx['user_id']
        clauses.append("a.mol_user_id = %(mine)s AND a.status = 'active'")
    if query:
        params['like'] = _like(query.strip())
        clauses.append("(a.name ILIKE %(like)s OR a.inventory_number ILIKE %(like)s "
                       "OR COALESCE(a.serial_number, '') ILIKE %(like)s OR COALESCE(a.barcode, '') ILIKE %(like)s "
                       "OR COALESCE(a.mol_name, '') ILIKE %(like)s)")
    for key, column, value in (('category_id', 'a.category_id', category_id),
                               ('department_id', 'a.department_id', department_id),
                               ('mol_user_id', 'a.mol_user_id', mol_user_id)):
        if value:
            params[key] = int(value)
            clauses.append('%s = %%(%s)s' % (column, key))
    return clauses, params


def list_assets(cursor, ctx, *, limit=50, offset=0, slice_code=None, **filters):
    clauses, params = _asset_filters(ctx, **filters)
    if slice_code in ASSET_SLICES:
        clauses.append(ASSET_SLICES[slice_code])
    sql = _ASSET_SELECT.replace('SELECT ', 'SELECT COUNT(*) OVER () AS total, ', 1)
    sql += ' WHERE ' + ' AND '.join('(%s)' % clause for clause in clauses)
    sql += " ORDER BY (a.status = 'written_off'), a.id DESC"
    if limit:
        params['limit'] = int(limit)
        params['offset'] = int(offset or 0)
        sql += ' LIMIT %(limit)s OFFSET %(offset)s'
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    total = int(rows[0][0]) if rows else 0
    return total, [_asset_row(row[1:]) for row in rows]


def asset_counters(cursor, ctx, **filters):
    """Счётчики плиток — по тому же отбору, но без самого среза."""
    clauses, params = _asset_filters(ctx, **filters)
    selects = ', '.join('COUNT(*) FILTER (WHERE %s)' % condition for condition in ASSET_SLICES.values())
    cursor.execute('SELECT COUNT(*), %s FROM fa_assets a WHERE %s'
                   % (selects, ' AND '.join('(%s)' % clause for clause in clauses)), params)
    row = cursor.fetchone() or ()
    counters = {'all': int(row[0] or 0) if row else 0}
    for index, code in enumerate(ASSET_SLICES, start=1):
        counters[code] = int(row[index] or 0) if row else 0
    return counters


def asset_facets(cursor, ctx):
    """Отделы, в которых есть видимые зрителю ОС, — для фильтра реестра."""
    scope_sql, params = asset_scope(ctx)
    cursor.execute(
        "SELECT DISTINCT a.department_id, a.department_name FROM fa_assets a "
        "WHERE a.department_id IS NOT NULL AND (%s) ORDER BY a.department_name" % scope_sql, params)
    return {'departments': [{'id': row[0], 'name': row[1]} for row in cursor.fetchall()]}


def read_asset(cursor, asset_id, *, for_update=False):
    cursor.execute(_ASSET_SELECT + ' WHERE a.id = %s' + (' FOR UPDATE OF a' if for_update else ''),
                   (int(asset_id),))
    row = cursor.fetchone()
    return _asset_row(row) if row else None


def read_assets(cursor, asset_ids, *, for_update=False):
    """ОС по списку id в порядке списка."""
    ids = [int(value) for value in asset_ids or ()]
    if not ids:
        return []
    cursor.execute(_ASSET_SELECT + ' WHERE a.id = ANY(%s::int[]) ORDER BY a.id'
                   + (' FOR UPDATE OF a' if for_update else ''), (ids,))
    found = {row[0]: _asset_row(row) for row in cursor.fetchall()}
    return [found[asset_id] for asset_id in ids if asset_id in found]


def inventory_taken(cursor, inventory_number, *, exclude_id=None):
    """Занят ли инвентарный номер другой карточкой (без учёта регистра)."""
    cursor.execute(
        "SELECT id, name FROM fa_assets WHERE lower(inventory_number) = lower(%s) AND id <> %s LIMIT 1",
        (str(inventory_number or '').strip(), int(exclude_id or 0)))
    row = cursor.fetchone()
    return {'id': row[0], 'name': row[1]} if row else None


def barcode_taken(cursor, barcode, *, exclude_id=None):
    """Привязан ли штрихкод к другой карточке (без учёта регистра)."""
    cursor.execute(
        "SELECT id, name, inventory_number FROM fa_assets WHERE lower(barcode) = lower(%s) AND id <> %s LIMIT 1",
        (str(barcode or '').strip(), int(exclude_id or 0)))
    row = cursor.fetchone()
    return {'id': row[0], 'name': row[1], 'inventory_number': row[2]} if row else None


def _folded(column):
    """Столбец в виде rules.fold_code: без пробелов, строчными, кириллица-двойник — латиницей."""
    return ("translate(regexp_replace(lower(%s), '\\s', '', 'g'), '%s', '%s')"
            % (column, rules.LOOKALIKE_CYRILLIC, rules.LOOKALIKE_LATIN))


def find_assets_by_code(cursor, code, *, limit=10):
    """ОС по прочитанному сканером коду: штрихкод наклейки, инвентарный или серийный номер. Обе стороны
    сравниваются в одном виде (rules.fold_code): номер в карточке мог быть заведён с пробелом или кириллицей,
    а сканер и ручной ввод дают код без пробелов и как попало по раскладке. Права смотрит вызывающий
    (access.asset_level): здесь — все совпадения."""
    barcodes = rules.barcode_variants(code)
    folded = rules.fold_code(code)
    if not folded:
        return []
    by_barcode = '%s = ANY(%%(barcodes)s)' % _folded('a.barcode')
    cursor.execute(
        _ASSET_SELECT + " WHERE " + by_barcode + " OR " + _folded('a.inventory_number') + " = %(code)s "
                        "OR " + _folded('a.serial_number') + " = %(code)s "
                        "ORDER BY (" + by_barcode + ") DESC NULLS LAST, (a.status = 'written_off'), "
                        "a.id DESC LIMIT %(limit)s",
        {'barcodes': barcodes, 'code': folded, 'limit': int(limit)})
    return [_asset_row(row) for row in cursor.fetchall()]


def set_barcode(cursor, asset_id, barcode):
    cursor.execute("UPDATE fa_assets SET barcode = %%s, updated_at = %s WHERE id = %%s" % _NOW,
                   (barcode, int(asset_id)))


def last_inventory_number(cursor):
    cursor.execute("SELECT inventory_number FROM fa_assets ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    return row[0] if row else None


def create_asset(cursor, fields, holder, actor):
    """Заводит карточку ОС — сразу «На учёте» и за своим МОЛ (ТЗ, процесс 1)."""
    cursor.execute(
        """
        INSERT INTO fa_assets (inventory_number, name, category_id, serial_number, barcode, cost, acquired_on, note,
                               mol_user_id, mol_name, department_id, department_name, location, on_stock,
                               created_by, created_by_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (fields['inventory_number'], fields['name'], fields['category_id'], fields.get('serial_number'),
         fields.get('barcode'), fields.get('cost'), fields['acquired_on'], fields.get('note'),
         holder['user_id'], holder['name'], holder.get('department_id'), holder.get('department_name'),
         holder.get('location'), bool(holder.get('on_stock')), actor['id'], actor['name']))
    return cursor.fetchone()[0]


def correct_asset(cursor, asset_id, fields):
    """Правит описание карточки (только при открытом приёме — проверяет routes)."""
    cols = [name for name in rules.ASSET_CORRECTABLE if name in fields]
    if not cols:
        return
    cursor.execute(
        "UPDATE fa_assets SET %s, updated_at = %s WHERE id = %%s"
        % (', '.join('%s = %%s' % name for name in cols), _NOW),
        [fields[name] for name in cols] + [int(asset_id)])


def user_assets(cursor, user_id):
    """ОС, числящиеся за сотрудником сейчас."""
    cursor.execute(_ASSET_SELECT + " WHERE a.mol_user_id = %s AND a.status = 'active' ORDER BY a.id",
                   (int(user_id),))
    return [_asset_row(row) for row in cursor.fetchall()]


# ─── Запросы ─────────────────────────────────────────────────────────────────

REQUEST_FIELDS = (
    'id', 'kind', 'status', 'review_by', 'review_department_id', 'created_by', 'created_by_name',
    'from_user_id', 'from_name', 'from_department_id', 'from_department_name',
    'to_user_id', 'to_name', 'to_department_id', 'to_department_name', 'to_location', 'to_stock',
    'comment', 'need_title', 'need_category_id', 'need_for_user_id', 'need_for_name', 'source_request_id',
    'checked_by', 'checked_by_name', 'checked_at', 'check_comment',
    'act_date', 'signed_on', 'scan_file_id', 'scan_by', 'scan_by_name', 'scan_at',
    'original_handed_by', 'original_handed_by_name', 'original_handed_on',
    'original_received_by', 'original_received_by_name', 'original_received_at',
    'rejected_by', 'rejected_by_name', 'rejected_at', 'reject_reason', 'rejected_from',
    'submitted_at', 'closed_at', 'created_at', 'updated_at',
)
# Поля запроса, которые автор задаёт формой (черновик).
REQUEST_WRITE = (
    'from_user_id', 'from_name', 'from_department_id', 'from_department_name',
    'to_user_id', 'to_name', 'to_department_id', 'to_department_name', 'to_location', 'to_stock',
    'comment', 'need_title', 'need_category_id', 'need_for_user_id', 'need_for_name',
)

_REQUEST_SELECT = """
    SELECT %s,
           (SELECT COUNT(*) FROM fa_request_items i WHERE i.request_id = r.id),
           (SELECT string_agg(COALESCE(i.name, a.name), ', ' ORDER BY i.position, i.id)
              FROM fa_request_items i JOIN fa_assets a ON a.id = i.asset_id WHERE i.request_id = r.id),
           nc.name
      FROM fa_requests r
      LEFT JOIN fa_categories nc ON nc.id = r.need_category_id
""" % _cols('r', REQUEST_FIELDS)


def _request_row(row):
    size = len(REQUEST_FIELDS)
    request = dict(zip(REQUEST_FIELDS, row[:size]))
    request['items_count'] = int(row[size] or 0)
    request['items_title'] = row[size + 1]
    request['need_category_name'] = row[size + 2]
    request['kind_label'] = rules.KIND_LABELS.get(request['kind'], request['kind'])
    request['status_label'] = rules.status_label(request)
    return request


def request_scope(ctx):
    """Какие запросы видит зритель: (условие SQL, параметры). Двойник access.can_view_request."""
    params = {'viewer': ctx['user_id'], 'headed': sorted(access.headed_ids(ctx))}
    if access.is_super(ctx):
        return 'TRUE', params
    own_draft = "(r.status = 'draft' AND r.created_by = %(viewer)s)"
    if access.sees_all(ctx):
        return "(%s OR r.status <> 'draft')" % own_draft, params
    sides = ["%(viewer)s IN (r.created_by, r.from_user_id, r.to_user_id, r.need_for_user_id)",
             "r.from_department_id = ANY(%(headed)s::int[])",
             "r.to_department_id = ANY(%(headed)s::int[])",
             "r.review_department_id = ANY(%(headed)s::int[])"]
    if access.is_it(ctx):
        sides.append("r.review_by = 'it' OR r.kind = 'need'")
    return ("(%s OR (r.status <> 'draft' AND (%s)))"
            % (own_draft, ' OR '.join('COALESCE((%s), FALSE)' % side for side in sides))), params


def read_request(cursor, request_id, *, for_update=False):
    cursor.execute(_REQUEST_SELECT + ' WHERE r.id = %s' + (' FOR UPDATE OF r' if for_update else ''),
                   (int(request_id),))
    row = cursor.fetchone()
    return _request_row(row) if row else None


def _request_filters(ctx, *, status=None, kind=None, query=None, mine=False, asset_id=None, ids=None):
    """status — один статус или набор (плитка «В работе» — три статуса разом); ids — только эти
    запросы (доска в режиме «Ждут меня»: их отбирает правило access.is_waiting, а не SQL)."""
    scope_sql, params = request_scope(ctx)
    clauses = [scope_sql]
    if status:
        params['statuses'] = [status] if isinstance(status, str) else list(status)
        clauses.append('r.status = ANY(%(statuses)s)')
    if ids is not None:
        params['ids'] = [int(value) for value in ids]
        clauses.append('r.id = ANY(%(ids)s::int[])')
    if kind:
        params['kind'] = kind
        clauses.append('r.kind = %(kind)s')
    if mine:
        clauses.append('r.created_by = %(viewer)s')
    if asset_id:
        params['asset_id'] = int(asset_id)
        clauses.append('EXISTS (SELECT 1 FROM fa_request_items i WHERE i.request_id = r.id AND i.asset_id = %(asset_id)s)')
    if query:
        text = query.strip()
        params['like'] = _like(text)
        number = text.lstrip('№# ').strip()
        params['number'] = int(number) if number.isdigit() and len(number) < 10 else 0
        clauses.append(
            "(r.id = %(number)s OR COALESCE(r.from_name, '') ILIKE %(like)s OR COALESCE(r.to_name, '') ILIKE %(like)s "
            "OR COALESCE(r.created_by_name, '') ILIKE %(like)s OR COALESCE(r.need_title, '') ILIKE %(like)s "
            "OR EXISTS (SELECT 1 FROM fa_request_items i JOIN fa_assets a ON a.id = i.asset_id "
            "            WHERE i.request_id = r.id AND (a.name ILIKE %(like)s OR a.inventory_number ILIKE %(like)s "
            "                                           OR COALESCE(a.barcode, '') ILIKE %(like)s)))")
    return clauses, params


# Запросы в работе стоят выше закрытых и отклонённых; внутри — свежие сверху.
_REQUEST_ORDER = (" ORDER BY (r.status IN ('closed', 'rejected')), "
                  "COALESCE(r.submitted_at, r.created_at) DESC, r.id DESC")


def list_requests(cursor, ctx, *, limit=50, offset=0, **filters):
    clauses, params = _request_filters(ctx, **filters)
    sql = _REQUEST_SELECT.replace('SELECT ', 'SELECT COUNT(*) OVER () AS total, ', 1)
    sql += ' WHERE ' + ' AND '.join('(%s)' % clause for clause in clauses) + _REQUEST_ORDER
    if limit:
        params['limit'] = int(limit)
        params['offset'] = int(offset or 0)
        sql += ' LIMIT %(limit)s OFFSET %(offset)s'
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    total = int(rows[0][0]) if rows else 0
    return total, [_request_row(row[1:]) for row in rows]


# Доска запросов: колонка — статус, в колонке свежие сверху по моменту, когда запрос пришёл в
# свой статус (закрытые — по закрытию, ждущие оригинал — по скану), а не по дате создания.
SINCE_SQL = ("COALESCE(CASE r.status WHEN 'draft' THEN r.updated_at WHEN 'review' THEN r.submitted_at "
             "WHEN 'signing' THEN COALESCE(r.checked_at, r.submitted_at) WHEN 'original' THEN r.scan_at "
             "WHEN 'closed' THEN r.closed_at WHEN 'rejected' THEN r.rejected_at END, r.created_at)")
# Снимок на карточке доски — «ОС целиком» первого ОС из фото самого запроса.
_BOARD_PHOTO_JOIN = """
      LEFT JOIN LATERAL (
          SELECT f.id, f.bucket, f.blob_path, f.thumb_blob_path, f.content_type
            FROM fa_files f
           WHERE f.request_id = r.id AND f.kind = 'photo_whole'
           ORDER BY f.id
           LIMIT 1) ph ON TRUE
"""


def board_requests(cursor, ctx, *, status, limit, offset=0, **filters):
    """Колонка доски: (всего в колонке, запросы страницы). У запроса — since (когда пришёл в
    статус) и cover (снимок для карточки)."""
    clauses, params = _request_filters(ctx, status=status, **filters)
    sql = _REQUEST_SELECT.replace(
        'SELECT ', 'SELECT COUNT(*) OVER (), %s, ph.id, ph.bucket, ph.blob_path, ph.thumb_blob_path, '
                   'ph.content_type, ' % SINCE_SQL, 1)
    sql += _BOARD_PHOTO_JOIN + ' WHERE ' + ' AND '.join('(%s)' % clause for clause in clauses)
    sql += ' ORDER BY %s DESC, r.id DESC LIMIT %%(limit)s OFFSET %%(offset)s' % SINCE_SQL
    params['limit'] = int(limit)
    params['offset'] = int(offset or 0)
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    total = int(rows[0][0]) if rows else 0
    items = []
    for row in rows:
        request = _request_row(row[7:])
        request['since'] = row[1]
        request['cover'] = ({'id': row[2], 'bucket': row[3], 'blob_path': row[4], 'thumb_blob_path': row[5],
                             'content_type': row[6]} if row[2] else None)
        items.append(request)
    return total, items


def request_counters(cursor, ctx, **filters):
    """Счётчики плиток статусов — по тому же отбору, но без самого статуса."""
    filters.pop('status', None)
    clauses, params = _request_filters(ctx, **filters)
    cursor.execute('SELECT r.status, COUNT(*) FROM fa_requests r WHERE %s GROUP BY r.status'
                   % ' AND '.join('(%s)' % clause for clause in clauses), params)
    counters = {code: 0 for code in rules.STATUSES}
    total = 0
    for status, count in cursor.fetchall():
        total += int(count or 0)
        if status in counters:
            counters[status] = int(count or 0)
    counters['all'] = total
    return counters


def waiting_requests(cursor, ctx, *, limit=300):
    """Запросы, которые ждут действия этого человека. Правило — access.is_waiting; кандидаты
    (видимые и незавершённые) берутся из базы, их десятки."""
    scope_sql, params = request_scope(ctx)
    params['limit'] = int(limit)
    cursor.execute(_REQUEST_SELECT
                   + " WHERE (%s) AND r.status IN ('draft', 'review', 'signing', 'original')" % scope_sql
                   + _REQUEST_ORDER + ' LIMIT %(limit)s', params)
    return [request for request in (_request_row(row) for row in cursor.fetchall())
            if access.is_waiting(ctx, request)]


def create_request(cursor, kind, fields, actor, *, status=rules.STATUS_DRAFT, extra=None):
    values = {name: fields.get(name) for name in REQUEST_WRITE}
    values['to_stock'] = bool(values.get('to_stock'))
    values.update(extra or {})
    names = list(values)
    cursor.execute(
        "INSERT INTO fa_requests (kind, status, created_by, created_by_name, %s) VALUES (%%s, %%s, %%s, %%s, %s) "
        "RETURNING id" % (', '.join(names), ', '.join(['%s'] * len(names))),
        [kind, status, actor['id'], actor['name']] + [values[name] for name in names])
    return cursor.fetchone()[0]


def update_request(cursor, request_id, fields):
    names = [name for name in REQUEST_WRITE if name in fields]
    if not names:
        return
    values = [bool(fields[name]) if name == 'to_stock' else fields[name] for name in names]
    cursor.execute(
        "UPDATE fa_requests SET %s, updated_at = %s WHERE id = %%s"
        % (', '.join('%s = %%s' % name for name in names), _NOW), values + [int(request_id)])


def delete_draft(cursor, request_id):
    """Удаляет черновик вместе с его строками и файлами (вернёт блобы для уборки)."""
    cursor.execute("SELECT bucket, blob_path, thumb_blob_path FROM fa_files WHERE request_id = %s", (int(request_id),))
    refs = _blob_refs(cursor.fetchall())
    cursor.execute("DELETE FROM fa_requests WHERE id = %s AND status = 'draft'", (int(request_id),))
    return refs if cursor.rowcount else []


def _blob_refs(rows):
    refs = []
    for bucket, blob_path, thumb_blob_path in rows:
        refs.append((bucket, blob_path))
        if thumb_blob_path:
            refs.append((bucket, thumb_blob_path))
    return refs


# ─── ОС запроса ──────────────────────────────────────────────────────────────

ITEM_FIELDS = ('id', 'request_id', 'asset_id', 'position', 'name', 'inventory_number', 'serial_number',
               'category_name', 'before_mol_user_id', 'before_mol_name', 'before_department_id',
               'before_department_name', 'before_location', 'before_on_stock')


def read_items(cursor, request_id):
    """ОС запроса. Пока запрос — черновик, название и номер берутся из карточки; после
    отправки — из снимка (акт говорит о том, что было в тот день)."""
    cursor.execute(
        """
        SELECT %s, a.name, a.inventory_number, a.serial_number, c.name, c.check_by, a.status,
               a.mol_user_id, a.mol_name, a.department_id, a.department_name, a.location, a.on_stock,
               a.open_request_id, a.cost, a.acquired_on
          FROM fa_request_items i
          JOIN fa_assets a ON a.id = i.asset_id
          JOIN fa_categories c ON c.id = a.category_id
         WHERE i.request_id = %%s
         ORDER BY i.position, i.id
        """ % _cols('i', ITEM_FIELDS), (int(request_id),))
    items = []
    size = len(ITEM_FIELDS)
    for row in cursor.fetchall():
        item = dict(zip(ITEM_FIELDS, row[:size]))
        (name, inventory_number, serial_number, category_name, check_by, status,
         mol_user_id, mol_name, department_id, department_name, location, on_stock, open_request_id,
         cost, acquired_on) = row[size:]
        item['name'] = item['name'] or name
        item['inventory_number'] = item['inventory_number'] or inventory_number
        item['serial_number'] = item['serial_number'] if item['serial_number'] is not None else serial_number
        item['category_name'] = item['category_name'] or category_name
        item['check_by'] = check_by
        item['asset'] = {'status': status, 'mol_user_id': mol_user_id, 'mol_name': mol_name,
                         'department_id': department_id, 'department_name': department_name,
                         'location': location, 'on_stock': bool(on_stock), 'open_request_id': open_request_id,
                         # Для печатной формы списания (ДА-3): после приёма они не меняются.
                         'cost': cost, 'acquired_on': acquired_on}
        items.append(item)
    return items


def set_items(cursor, request_id, asset_ids):
    """Состав черновика: ОС в порядке выбора. Файлы убранных ОС возвращаются для уборки."""
    ids = [int(value) for value in asset_ids or ()]
    cursor.execute("SELECT bucket, blob_path, thumb_blob_path FROM fa_files "
                   "WHERE request_id = %s AND asset_id IS NOT NULL AND NOT (asset_id = ANY(%s::int[]))",
                   (int(request_id), ids))
    refs = _blob_refs(cursor.fetchall())
    cursor.execute("DELETE FROM fa_files WHERE request_id = %s AND asset_id IS NOT NULL "
                   "AND NOT (asset_id = ANY(%s::int[]))", (int(request_id), ids))
    cursor.execute("DELETE FROM fa_request_items WHERE request_id = %s AND NOT (asset_id = ANY(%s::int[]))",
                   (int(request_id), ids))
    for position, asset_id in enumerate(ids, start=1):
        cursor.execute(
            "INSERT INTO fa_request_items (request_id, asset_id, position) VALUES (%s, %s, %s) "
            "ON CONFLICT (request_id, asset_id) DO UPDATE SET position = EXCLUDED.position",
            (int(request_id), asset_id, position))
    return refs


def snapshot_items(cursor, request_id):
    """Снимок ОС на день отправки: название, номера, у кого и где было."""
    cursor.execute(
        """
        UPDATE fa_request_items i
           SET name = a.name, inventory_number = a.inventory_number, serial_number = a.serial_number,
               category_name = c.name, before_mol_user_id = a.mol_user_id, before_mol_name = a.mol_name,
               before_department_id = a.department_id, before_department_name = a.department_name,
               before_location = a.location, before_on_stock = a.on_stock
          FROM fa_assets a JOIN fa_categories c ON c.id = a.category_id
         WHERE i.request_id = %s AND a.id = i.asset_id
        """, (int(request_id),))


# ─── Файлы ───────────────────────────────────────────────────────────────────

FILE_FIELDS = ('id', 'request_id', 'asset_id', 'kind', 'bucket', 'blob_path', 'thumb_blob_path', 'file_name',
               'content_type', 'file_size', 'width', 'height', 'thumb_width', 'thumb_height',
               'uploaded_by', 'uploaded_by_name', 'created_at')


def add_file(cursor, request_id, asset_id, kind, stored, actor):
    cursor.execute(
        """
        INSERT INTO fa_files (request_id, asset_id, kind, bucket, blob_path, thumb_blob_path, file_name,
                              content_type, file_size, width, height, thumb_width, thumb_height,
                              uploaded_by, uploaded_by_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (int(request_id), asset_id, kind, stored['bucket'], stored['blob_path'], stored.get('thumb_blob_path'),
         stored.get('file_name'), stored.get('content_type'), stored.get('file_size'), stored.get('width'),
         stored.get('height'), stored.get('thumb_width'), stored.get('thumb_height'), actor['id'], actor['name']))
    return cursor.fetchone()[0]


def read_files(cursor, request_id):
    cursor.execute("SELECT %s FROM fa_files f WHERE f.request_id = %%s ORDER BY f.id" % _cols('f', FILE_FIELDS),
                   (int(request_id),))
    return [dict(zip(FILE_FIELDS, row)) for row in cursor.fetchall()]


def read_file(cursor, file_id):
    cursor.execute("SELECT %s FROM fa_files f WHERE f.id = %%s" % _cols('f', FILE_FIELDS), (int(file_id),))
    row = cursor.fetchone()
    return dict(zip(FILE_FIELDS, row)) if row else None


def drop_files(cursor, request_id, file_ids):
    """Убирает фото из черновика; возвращает блобы для уборки после фиксации."""
    ids = [int(value) for value in file_ids or ()]
    if not ids:
        return []
    cursor.execute("DELETE FROM fa_files WHERE request_id = %s AND id = ANY(%s::int[]) AND kind <> 'act_scan' "
                   "RETURNING bucket, blob_path, thumb_blob_path", (int(request_id), ids))
    return _blob_refs(cursor.fetchall())


def asset_photos(cursor, asset_id, *, limit=24):
    """Фото ОС из отправленных запросов — свежие сверху."""
    cursor.execute(
        "SELECT %s FROM fa_files f JOIN fa_requests r ON r.id = f.request_id "
        "WHERE f.asset_id = %%s AND f.kind <> 'act_scan' AND r.status <> 'draft' "
        "ORDER BY f.request_id DESC, "
        "         CASE f.kind WHEN 'photo_whole' THEN 0 WHEN 'photo_number' THEN 1 ELSE 2 END, f.id "
        "LIMIT %%s" % _cols('f', FILE_FIELDS), (int(asset_id), int(limit)))
    return [dict(zip(FILE_FIELDS, row)) for row in cursor.fetchall()]


# ─── Движение запроса ────────────────────────────────────────────────────────

def hold_assets(cursor, request_id, asset_ids):
    cursor.execute("UPDATE fa_assets SET open_request_id = %s, updated_at = " + _NOW
                   + " WHERE id = ANY(%s::int[])", (int(request_id), [int(value) for value in asset_ids]))


def release_assets(cursor, request_id):
    cursor.execute("UPDATE fa_assets SET open_request_id = NULL, updated_at = " + _NOW
                   + " WHERE open_request_id = %s", (int(request_id),))


def mark_submitted(cursor, request_id, *, status, review_by=None, review_department_id=None, holder=None,
                   act_date=None):
    """Запрос ушёл от автора: статус, проверяющий, сторона «сдал» со снимком имён."""
    holder = holder or {}
    cursor.execute(
        """
        UPDATE fa_requests
           SET status = %s, review_by = %s, review_department_id = %s,
               from_user_id = COALESCE(%s, from_user_id), from_name = COALESCE(%s, from_name),
               from_department_id = COALESCE(%s, from_department_id),
               from_department_name = COALESCE(%s, from_department_name),
               act_date = %s, submitted_at = """ + _NOW + """, updated_at = """ + _NOW + """
         WHERE id = %s
        """,
        (status, review_by, review_department_id, holder.get('user_id'), holder.get('name'),
         holder.get('department_id'), holder.get('department_name'), act_date, int(request_id)))


def mark_checked(cursor, request_id, actor, comment, act_date):
    cursor.execute(
        "UPDATE fa_requests SET status = 'signing', checked_by = %s, checked_by_name = %s, checked_at = " + _NOW
        + ", check_comment = %s, act_date = %s, updated_at = " + _NOW + " WHERE id = %s",
        (actor['id'], actor['name'], comment, act_date, int(request_id)))


def mark_scan(cursor, request_id, file_id, signed_on, actor):
    cursor.execute(
        "UPDATE fa_requests SET status = 'original', scan_file_id = %s, signed_on = %s, scan_by = %s, "
        "scan_by_name = %s, scan_at = " + _NOW + ", updated_at = " + _NOW + " WHERE id = %s",
        (int(file_id), signed_on, actor['id'], actor['name'], int(request_id)))


def mark_closed(cursor, request_id, *, handed_by=None, handed_on=None, actor=None):
    """«Оригинал получен» и закрытие запроса. Для «Нового ОС» оригинала нет — закрывает приём."""
    handed_by = handed_by or {}
    cursor.execute(
        """
        UPDATE fa_requests
           SET status = 'closed', closed_at = """ + _NOW + """, updated_at = """ + _NOW + """,
               original_handed_by = %s, original_handed_by_name = %s, original_handed_on = %s,
               original_received_by = %s, original_received_by_name = %s,
               original_received_at = CASE WHEN %s THEN """ + _NOW + """ ELSE NULL END
         WHERE id = %s
        """,
        (handed_by.get('id'), handed_by.get('name'), handed_on,
         (actor or {}).get('id') if handed_on else None, (actor or {}).get('name') if handed_on else None,
         bool(handed_on), int(request_id)))


def mark_rejected(cursor, request_id, *, reason, from_status, actor):
    cursor.execute(
        "UPDATE fa_requests SET status = 'rejected', rejected_by = %s, rejected_by_name = %s, rejected_at = " + _NOW
        + ", reject_reason = %s, rejected_from = %s, updated_at = " + _NOW + " WHERE id = %s",
        (actor['id'], actor['name'], reason, from_status, int(request_id)))


def apply_move(cursor, request, items, actor):
    """Закрытый запрос на перемещение меняет карточки: новый МОЛ, отдел, место."""
    base = {'mol_user_id': request.get('to_user_id'), 'mol_name': request.get('to_name'),
            'department_id': request.get('to_department_id'), 'department_name': request.get('to_department_name'),
            'on_stock': bool(request.get('to_stock'))}
    for item in items:
        # Новое место не указано — ОС остаётся там же: пустое поле не стирает, где оно стоит.
        target = dict(base, location=request.get('to_location') or item.get('before_location'))
        cursor.execute(
            "UPDATE fa_assets SET mol_user_id = %s, mol_name = %s, department_id = %s, department_name = %s, "
            "location = %s, on_stock = %s, open_request_id = NULL, updated_at = " + _NOW
            + " WHERE id = %s AND open_request_id = %s",
            (target['mol_user_id'], target['mol_name'], target['department_id'], target['department_name'],
             target['location'], target['on_stock'], item['asset_id'], request['id']))
        add_event(cursor, 'moved', actor, asset_id=item['asset_id'], request_id=request['id'], payload={
            'from': {'mol_user_id': item.get('before_mol_user_id'), 'mol_name': item.get('before_mol_name'),
                     'department_name': item.get('before_department_name'), 'location': item.get('before_location'),
                     'on_stock': bool(item.get('before_on_stock'))},
            'to': {'mol_user_id': target['mol_user_id'], 'mol_name': target['mol_name'],
                   'department_name': target['department_name'], 'location': target['location'],
                   'on_stock': target['on_stock']},
            'signed_on': request.get('signed_on'),
        })


def apply_writeoff(cursor, request, items, actor):
    """Закрытый запрос на списание переводит ОС в «Списано»."""
    for item in items:
        cursor.execute(
            "UPDATE fa_assets SET status = 'written_off', written_off_at = " + _NOW + ", on_stock = FALSE, "
            "open_request_id = NULL, updated_at = " + _NOW + " WHERE id = %s AND open_request_id = %s",
            (item['asset_id'], request['id']))
        add_event(cursor, 'written_off', actor, asset_id=item['asset_id'], request_id=request['id'], payload={
            'reason': request.get('comment'), 'signed_on': request.get('signed_on'),
            'mol_name': item.get('before_mol_name'),
        })


# ─── История ─────────────────────────────────────────────────────────────────

EVENT_FIELDS = ('id', 'asset_id', 'request_id', 'kind', 'actor_user_id', 'actor_name', 'payload', 'created_at')


def add_event(cursor, kind, actor, *, asset_id=None, request_id=None, payload=None):
    """Строка истории. Только INSERT: править и удалять историю не даёт триггер базы."""
    cursor.execute(
        "INSERT INTO fa_events (asset_id, request_id, kind, actor_user_id, actor_name, payload) "
        "VALUES (%s, %s, %s, %s, %s, %s::jsonb) RETURNING id",
        (asset_id, request_id, kind, (actor or {}).get('id'), (actor or {}).get('name'), _json(payload)))
    return cursor.fetchone()[0]


def _event_row(row):
    event = dict(zip(EVENT_FIELDS, row[:len(EVENT_FIELDS)]))
    payload = event.get('payload')
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    event['payload'] = payload if isinstance(payload, dict) else {}
    event['label'] = rules.event_label(event['kind'], event['payload'])
    return event


def request_events(cursor, request_id):
    cursor.execute("SELECT %s FROM fa_events e WHERE e.request_id = %%s ORDER BY e.created_at, e.id"
                   % _cols('e', EVENT_FIELDS),
                   (int(request_id),))
    return [_event_row(row) for row in cursor.fetchall()]


def asset_events(cursor, asset_id, *, participant_id=None):
    """История ОС: его собственные события и события запросов, в которых оно участвует.

    participant_id — МОЛ смотрит своё ОС: ему видна только та часть истории, где участвует он
    сам (сдал, принял, сделал запись). Что было с ОС у прежних владельцев, он не видит."""
    params = {'asset': int(asset_id)}
    own = ''
    if participant_id is not None:
        params['viewer'] = int(participant_id)
        own = (" AND (e.actor_user_id = %(viewer)s OR COALESCE("
               "%(viewer)s IN (r.created_by, r.from_user_id, r.to_user_id), FALSE))")
    cursor.execute(
        """
        SELECT %s, r.kind
          FROM fa_events e
          LEFT JOIN fa_requests r ON r.id = e.request_id
         WHERE (e.asset_id = %%(asset)s
                OR (e.asset_id IS NULL AND e.request_id IN (
                        SELECT i.request_id FROM fa_request_items i WHERE i.asset_id = %%(asset)s)))%s
         ORDER BY e.created_at DESC, e.id DESC
        """ % (_cols('e', EVENT_FIELDS), own), params)
    events = []
    for row in cursor.fetchall():
        event = _event_row(row)
        event['request_kind'] = row[len(EVENT_FIELDS)]
        event['request_kind_label'] = rules.KIND_LABELS.get(event['request_kind'])
        events.append(event)
    return events


def event_scope(ctx):
    """Какие записи истории видит зритель: (условие SQL, параметры).

    Бухгалтерия, HR и руководитель аналитики — всё (ТЗ, «Доступ к истории»). Остальные —
    записи запросов, которые им видны (они сторона акта, автор, руководитель стороны,
    проверяющий), и то, что сделали сами. Для сотрудника с ОС это и есть «история, где
    участвует он»."""
    if access.sees_all(ctx):
        return 'TRUE', {}
    scope_sql, params = request_scope(ctx)
    return '(e.actor_user_id = %%(viewer)s OR (r.id IS NOT NULL AND %s))' % scope_sql, params


def journal(cursor, ctx, *, limit=50, offset=0, kind=None, query=None, date_from=None, date_to=None):
    """Журнал раздела в пределах того, что видит зритель (event_scope)."""
    scope_sql, params = event_scope(ctx)
    clauses = [scope_sql]
    params.update({'limit': int(limit), 'offset': int(offset or 0)})
    if kind:
        params['kind'] = kind
        clauses.append('e.kind = %(kind)s')
    if date_from:
        params['date_from'] = date_from
        clauses.append('e.created_at >= %(date_from)s')
    if date_to:
        params['date_to'] = date_to + timedelta(days=1)
        clauses.append('e.created_at < %(date_to)s')
    if query:
        text = query.strip()
        params['like'] = _like(text)
        number = text.lstrip('№# ').strip()
        params['number'] = int(number) if number.isdigit() and len(number) < 10 else 0
        clauses.append(
            "(COALESCE(e.actor_name, '') ILIKE %(like)s OR COALESCE(a.name, '') ILIKE %(like)s "
            "OR COALESCE(a.inventory_number, '') ILIKE %(like)s OR COALESCE(a.barcode, '') ILIKE %(like)s "
            "OR e.request_id = %(number)s "
            "OR COALESCE(r.from_name, '') ILIKE %(like)s OR COALESCE(r.to_name, '') ILIKE %(like)s)")
    cursor.execute(
        """
        SELECT COUNT(*) OVER () AS total, %s, a.name, a.inventory_number, r.kind,
               (SELECT COUNT(*) FROM fa_request_items i WHERE i.request_id = e.request_id),
               (SELECT string_agg(COALESCE(i.name, ia.name), ', ' ORDER BY i.position, i.id)
                  FROM fa_request_items i JOIN fa_assets ia ON ia.id = i.asset_id
                 WHERE i.request_id = e.request_id),
               a.mol_user_id, a.department_id
          FROM fa_events e
          LEFT JOIN fa_assets a ON a.id = e.asset_id
          LEFT JOIN fa_requests r ON r.id = e.request_id
         WHERE %s
         ORDER BY e.created_at DESC, e.id DESC
         LIMIT %%(limit)s OFFSET %%(offset)s
        """ % (_cols('e', EVENT_FIELDS), ' AND '.join('(%s)' % clause for clause in clauses)), params)
    rows = cursor.fetchall()
    total = int(rows[0][0]) if rows else 0
    size = len(EVENT_FIELDS)
    events = []
    for row in rows:
        event = _event_row(row[1:])
        event['asset_name'], event['inventory_number'], event['request_kind'] = row[1 + size:4 + size]
        event['request_kind_label'] = rules.KIND_LABELS.get(event['request_kind'])
        event['items_count'] = int(row[4 + size] or 0)
        event['items_title'] = row[5 + size]
        # Карточку ОС из журнала открывает только тот, кому она доступна: у бывшего МОЛ запись
        # о передаче в истории остаётся, а само ОС уже чужое.
        event['asset_open'] = bool(event.get('asset_id')) and bool(access.asset_level(
            ctx, {'mol_user_id': row[6 + size], 'department_id': row[7 + size]}))
        events.append(event)
    return total, events


# ─── Увольнение ──────────────────────────────────────────────────────────────

def dismissal_blockers(cursor, user_id):
    """ОС сотрудника БЕЗ подписанного акта — пока они есть, приказ об увольнении недоступен.

    ОС не держит увольнение, когда по нему приложен скан подписанного акта (запрос «ждёт
    оригинал»), а сам запрос уводит ОС от сотрудника: списывает или передаёт другому."""
    cursor.execute(
        """
        SELECT a.id, a.name, a.inventory_number, r.id, r.kind, r.status
          FROM fa_assets a
          LEFT JOIN fa_requests r ON r.id = a.open_request_id
         WHERE a.mol_user_id = %(user)s AND a.status = 'active'
           AND NOT COALESCE(
                   r.status = 'original'
                   AND (r.kind = 'writeoff'
                        OR (r.kind IN ('transfer', 'dismissal') AND r.to_user_id IS DISTINCT FROM a.mol_user_id)),
                   FALSE)
         ORDER BY a.id
        """, {'user': int(user_id)})
    return [{'id': row[0], 'name': row[1], 'inventory_number': row[2],
             'request': ({'id': row[3], 'kind': row[4], 'status': row[5],
                          'status_label': rules.STATUS_LABELS.get(row[5], row[5])} if row[3] else None)}
            for row in cursor.fetchall()]


def open_dismissal_request(cursor, user_id):
    """Незавершённая передача при увольнении этого сотрудника (включая черновик HR)."""
    cursor.execute(
        _REQUEST_SELECT + " WHERE r.kind = 'dismissal' AND r.from_user_id = %s "
        "AND r.status IN ('draft', 'review', 'signing', 'original') ORDER BY r.id DESC LIMIT 1", (int(user_id),))
    row = cursor.fetchone()
    return _request_row(row) if row else None


def dismissal_board(cursor):
    """Вкладка HR «Увольнение»: чьи передачи идут и чьи закрыты за последние 30 дней.
    Передачу запускает сам HR — сотрудник его не «вызывает» (решение владельца 17.11.2026)."""
    cursor.execute(
        """
        WITH people AS (
            SELECT DISTINCT r.from_user_id AS user_id FROM fa_requests r
             WHERE r.kind = 'dismissal' AND r.from_user_id IS NOT NULL
               AND (r.status IN ('draft', 'review', 'signing', 'original')
                    OR (r.status = 'closed' AND r.closed_at > """ + _NOW + """ - INTERVAL '30 days'))
        )
        SELECT u.id, u.name, d.name, u.job_title, u.status,
               (SELECT COUNT(*) FROM fa_assets a WHERE a.mol_user_id = u.id AND a.status = 'active')
          FROM people p
          JOIN users u ON u.id = p.user_id
          LEFT JOIN departments d ON d.id = u.department_id
         ORDER BY lower(u.name), u.id
        """)
    return [{'user_id': row[0], 'name': row[1], 'department_name': row[2], 'job_title': row[3],
             'fired': str(row[4] or '').lower() in _FIRED, 'assets': int(row[5] or 0)}
            for row in cursor.fetchall()]


def last_dismissal_request(cursor, user_id):
    cursor.execute(_REQUEST_SELECT + " WHERE r.kind = 'dismissal' AND r.from_user_id = %s "
                   "ORDER BY r.id DESC LIMIT 1", (int(user_id),))
    row = cursor.fetchone()
    return _request_row(row) if row else None


def incoming_items(cursor, user_id):
    """ОС, которые передаются этому человеку и ещё не дошли (акт подписывается или ждёт оригинал)."""
    cursor.execute(
        """
        SELECT i.asset_id, COALESCE(i.name, a.name), COALESCE(i.inventory_number, a.inventory_number),
               r.id, r.kind, r.status, r.from_name
          FROM fa_requests r
          JOIN fa_request_items i ON i.request_id = r.id
          JOIN fa_assets a ON a.id = i.asset_id
         WHERE r.to_user_id = %s AND r.kind IN ('transfer', 'dismissal') AND r.status IN ('signing', 'original')
           AND a.mol_user_id IS DISTINCT FROM r.to_user_id
         ORDER BY r.id DESC, i.position
        """, (int(user_id),))
    return [{'asset_id': row[0], 'name': row[1], 'inventory_number': row[2], 'request_id': row[3],
             'kind': row[4], 'status': row[5], 'status_label': rules.STATUS_LABELS.get(row[5], row[5]),
             'from_name': row[6]} for row in cursor.fetchall()]
