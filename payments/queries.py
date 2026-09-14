"""SQL раздела «Оплата счетов». Только запросы и разбор строк, без Flask.

Соглашения:

* SELECT и разбор строки собираются из ОДНОГО списка полей (`_REQUEST_FIELDS` и
  т. п.) — позиционная раскладка на сорока колонках ждёт своего часа, это
  уже стоило 500-х «Посылкам».
* Время в базе — настенные часы Алматы (`_NOW`), наружу — isoformat без зоны
  (`plain()`), иначе jsonify припишет «GMT» и браузер уведёт даты на +5 часов.
* Деньги — Decimal в базе, float наружу: фронту нужны числа, а тенге в float
  до сотен миллиардов представляются точно с копейками.
* Маршрут материализован: `create_request` заводит 12 строк шагов сразу,
  дальше запросы двигают состояние по ним, а не пересчитывают.
"""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from psycopg2.extras import Json

from . import workflow

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"
_ALMATY_OFFSET = timedelta(hours=5)


def now_almaty():
    return datetime.utcnow() + _ALMATY_OFFSET


def today_almaty():
    return now_almaty().date()


def plain(value):
    """Рекурсивно приводит значение к тому, что jsonify отдаст без сюрпризов."""
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _map(fields, row):
    return dict(zip(fields, row)) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Профиль смотрящего
# ─────────────────────────────────────────────────────────────────────────────

_ACCESS_CONTEXT_SQL = """
    SELECT u.name, u.role, u.department_id, d.code, d.name, u.supervisor_id,
           COALESCE((SELECT array_agg(h.id) FROM departments h
                      WHERE h.head_user_id = u.id AND h.is_active), '{}'),
           (u.telegram_id IS NOT NULL)
      FROM users u
      LEFT JOIN departments d ON d.id = u.department_id
     WHERE u.id = %(user_id)s
"""


def load_access_context(cursor, user_id):
    cursor.execute(_ACCESS_CONTEXT_SQL, {'user_id': int(user_id)})
    row = cursor.fetchone()
    if not row or row[1] is None:
        return None
    name, role, department_id, department_code, department_name, supervisor_id, headed, has_tg = row
    return {
        'user_id': int(user_id),
        'name': name,
        'role': str(role or '').strip().lower(),
        'department_id': department_id,
        'department_code': department_code,
        'department_name': department_name,
        'supervisor_id': supervisor_id,
        'headed_department_ids': list(headed or []),
        'has_telegram': bool(has_tg),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Люди: участники ролей, руководитель, получатели уведомлений
# ─────────────────────────────────────────────────────────────────────────────

def list_users(cursor):
    """Сотрудники для выбора руководителя, участников ролей и делегата по Приказу.
    Уволенных нет: право согласовывать уволенному не выдают."""
    cursor.execute(
        """
        SELECT u.id, u.name, u.role, u.department_id, d.name, (u.telegram_id IS NOT NULL), u.job_title
          FROM users u
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE u.status <> 'fired'
         ORDER BY u.name, u.id
        """
    )
    return [{
        'id': row[0], 'name': row[1], 'role': row[2], 'department_id': row[3],
        'department_name': row[4], 'has_telegram': bool(row[5]), 'job_title': row[6],
    } for row in cursor.fetchall()]


def role_members(cursor):
    """{'founder': [{user_id, name, has_telegram, department_name}], 'accounting': [...]}"""
    cursor.execute(
        """
        SELECT m.role_code, m.user_id, u.name, (u.telegram_id IS NOT NULL), d.name, m.id
          FROM payment_role_members m
          JOIN users u ON u.id = m.user_id
          LEFT JOIN departments d ON d.id = u.department_id
         ORDER BY m.role_code, u.name
        """
    )
    result = {}
    for role_code, user_id, name, has_tg, dept, member_id in cursor.fetchall():
        result.setdefault(role_code, []).append({
            'id': member_id, 'user_id': user_id, 'name': name,
            'has_telegram': bool(has_tg), 'department_name': dept,
        })
    return result


def role_member_ids(cursor):
    return {role: {int(m['user_id']) for m in members} for role, members in role_members(cursor).items()}


def roles_of_user(cursor, user_id):
    cursor.execute("SELECT role_code FROM payment_role_members WHERE user_id = %s", (int(user_id),))
    return {row[0] for row in cursor.fetchall()}


def add_role_member(cursor, role_code, user_id, actor_id):
    cursor.execute(
        """
        INSERT INTO payment_role_members (role_code, user_id, added_by)
        VALUES (%s, %s, %s)
        ON CONFLICT (role_code, user_id) DO NOTHING
        """,
        (role_code, int(user_id), actor_id),
    )


def remove_role_member(cursor, role_code, user_id):
    cursor.execute("DELETE FROM payment_role_members WHERE role_code = %s AND user_id = %s",
                   (role_code, int(user_id)))


def user_brief(cursor, user_id):
    if not user_id:
        return None
    cursor.execute(
        """
        SELECT u.id, u.name, u.telegram_id, u.department_id, d.name
          FROM users u LEFT JOIN departments d ON d.id = u.department_id
         WHERE u.id = %s
        """,
        (int(user_id),),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': row[0], 'name': row[1], 'telegram_id': row[2],
            'department_id': row[3], 'department_name': row[4]}


def resolve_manager(cursor, user_id):
    """Непосредственный руководитель: users.supervisor_id, иначе глава отдела.

    Глава своего же отдела руководителем себе не считается. Нет ни того, ни
    другого — None: форма попросит выбрать руководителя руками, а если инициатор
    его не укажет, шаг 2 будет пропущен с пометкой (см. workflow.build_route).
    """
    cursor.execute(
        """
        SELECT u.supervisor_id, s.name, s.status, d.head_user_id, h.name, h.status
          FROM users u
          LEFT JOIN users s ON s.id = u.supervisor_id
          LEFT JOIN departments d ON d.id = u.department_id
          LEFT JOIN users h ON h.id = d.head_user_id
         WHERE u.id = %s
        """,
        (int(user_id),),
    )
    row = cursor.fetchone()
    if not row:
        return None
    supervisor_id, supervisor_name, supervisor_status, head_id, head_name, head_status = row
    if supervisor_id and supervisor_status != 'fired':
        return {'id': supervisor_id, 'name': supervisor_name, 'source': 'supervisor'}
    if head_id and head_id != int(user_id) and head_status != 'fired':
        return {'id': head_id, 'name': head_name, 'source': 'department_head'}
    return None


def telegram_recipients(cursor, *, user_ids=(), role_code=None, exclude=()):
    """[{user_id, name, chat_id}] — у кого есть Telegram среди названных людей
    или участников роли."""
    ids = {int(x) for x in user_ids if x}
    if role_code:
        cursor.execute("SELECT user_id FROM payment_role_members WHERE role_code = %s", (role_code,))
        ids |= {int(row[0]) for row in cursor.fetchall()}
    ids -= {int(x) for x in exclude if x}
    if not ids:
        return []
    cursor.execute(
        "SELECT id, name, telegram_id FROM users WHERE id = ANY(%s) AND telegram_id IS NOT NULL "
        "AND status <> 'fired'",
        (sorted(ids),),
    )
    return [{'user_id': row[0], 'name': row[1], 'chat_id': row[2]} for row in cursor.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Справочники
# ─────────────────────────────────────────────────────────────────────────────

def list_projects(cursor, include_inactive=False):
    cursor.execute(
        "SELECT id, name, is_active FROM payment_projects %s ORDER BY name"
        % ('' if include_inactive else 'WHERE is_active')
    )
    return [{'id': r[0], 'name': r[1], 'is_active': r[2]} for r in cursor.fetchall()]


def upsert_project(cursor, *, project_id=None, name, is_active=True, actor_id=None):
    if project_id:
        cursor.execute("UPDATE payment_projects SET name = %s, is_active = %s WHERE id = %s RETURNING id",
                       (name, bool(is_active), int(project_id)))
    else:
        cursor.execute(
            "INSERT INTO payment_projects (name, is_active, created_by) VALUES (%s, %s, %s) RETURNING id",
            (name, bool(is_active), actor_id))
    row = cursor.fetchone()
    return row[0] if row else None


def find_or_create_project(cursor, name, actor_id=None):
    cursor.execute("SELECT id FROM payment_projects WHERE lower(name) = lower(%s)", (name,))
    row = cursor.fetchone()
    if row:
        return row[0], False
    return upsert_project(cursor, name=name, actor_id=actor_id), True


def list_categories(cursor, include_inactive=False):
    cursor.execute(
        "SELECT id, parent_id, name, position, is_active FROM payment_categories %s "
        "ORDER BY parent_id NULLS FIRST, position, name"
        % ('' if include_inactive else 'WHERE is_active')
    )
    return [{'id': r[0], 'parent_id': r[1], 'name': r[2], 'position': r[3], 'is_active': r[4]}
            for r in cursor.fetchall()]


def upsert_category(cursor, *, category_id=None, parent_id=None, name, position=0, is_active=True,
                    actor_id=None):
    if category_id:
        cursor.execute(
            "UPDATE payment_categories SET parent_id = %s, name = %s, position = %s, is_active = %s "
            "WHERE id = %s RETURNING id",
            (parent_id, name, int(position or 0), bool(is_active), int(category_id)))
    else:
        cursor.execute(
            "INSERT INTO payment_categories (parent_id, name, position, is_active, created_by) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (parent_id, name, int(position or 0), bool(is_active), actor_id))
    row = cursor.fetchone()
    return row[0] if row else None


def find_or_create_category(cursor, name, parent_id=None, actor_id=None):
    cursor.execute(
        "SELECT id FROM payment_categories WHERE lower(name) = lower(%s) AND COALESCE(parent_id, 0) = %s",
        (name, int(parent_id or 0)))
    row = cursor.fetchone()
    if row:
        return row[0], False
    return upsert_category(cursor, parent_id=parent_id, name=name, actor_id=actor_id), True


_PARTY_FIELDS = ('id', 'name', 'bin', 'kind', 'vat_payer', 'note', 'is_active')


def list_legal_entities(cursor, include_inactive=False):
    cursor.execute(
        "SELECT %s FROM payment_legal_entities %s ORDER BY name"
        % (', '.join(_PARTY_FIELDS), '' if include_inactive else 'WHERE is_active'))
    return [_map(_PARTY_FIELDS, row) for row in cursor.fetchall()]


def upsert_legal_entity(cursor, *, entity_id=None, name, bin_code=None, kind=None, vat_payer=False,
                        note=None, is_active=True, actor_id=None):
    if entity_id:
        cursor.execute(
            "UPDATE payment_legal_entities SET name = %s, bin = %s, kind = %s, vat_payer = %s, "
            "note = %s, is_active = %s WHERE id = %s RETURNING id",
            (name, bin_code, kind, bool(vat_payer), note, bool(is_active), int(entity_id)))
    else:
        cursor.execute(
            "INSERT INTO payment_legal_entities (name, bin, kind, vat_payer, note, is_active, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (name, bin_code, kind, bool(vat_payer), note, bool(is_active), actor_id))
    row = cursor.fetchone()
    return row[0] if row else None


_COUNTERPARTY_FIELDS = ('id', 'name', 'bin', 'kind', 'vat_payer', 'requisites', 'contact', 'note',
                        'is_active')


def list_counterparties(cursor, include_inactive=False, query=None):
    clauses, params = [], []
    if not include_inactive:
        clauses.append('is_active')
    if query:
        clauses.append("(name ILIKE %s OR COALESCE(bin, '') ILIKE %s)")
        like = '%' + query + '%'
        params += [like, like]
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
    cursor.execute(
        "SELECT %s, (SELECT COUNT(*) FROM payment_contracts c WHERE c.counterparty_id = cp.id AND c.status = 'active') "
        "FROM payment_counterparties cp %s ORDER BY name"
        % (', '.join('cp.%s' % f for f in _COUNTERPARTY_FIELDS), where), params)
    result = []
    for row in cursor.fetchall():
        item = _map(_COUNTERPARTY_FIELDS, row[:-1])
        item['active_contracts'] = int(row[-1] or 0)
        result.append(item)
    return result


def upsert_counterparty(cursor, *, counterparty_id=None, name, bin_code=None, kind=None,
                        vat_payer=False, requisites=None, contact=None, note=None, is_active=True,
                        actor_id=None):
    if counterparty_id:
        cursor.execute(
            "UPDATE payment_counterparties SET name = %s, bin = %s, kind = %s, vat_payer = %s, "
            "requisites = %s, contact = %s, note = %s, is_active = %s WHERE id = %s RETURNING id",
            (name, bin_code, kind, bool(vat_payer), requisites, contact, note, bool(is_active),
             int(counterparty_id)))
    else:
        cursor.execute(
            "INSERT INTO payment_counterparties (name, bin, kind, vat_payer, requisites, contact, note, "
            "is_active, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (name, bin_code, kind, bool(vat_payer), requisites, contact, note, bool(is_active), actor_id))
    row = cursor.fetchone()
    return row[0] if row else None


def find_or_create_counterparty(cursor, name, actor_id=None):
    cursor.execute("SELECT id FROM payment_counterparties WHERE lower(name) = lower(%s)", (name,))
    row = cursor.fetchone()
    if row:
        return row[0], False
    return upsert_counterparty(cursor, name=name, actor_id=actor_id), True


_CONTRACT_FIELDS = ('id', 'counterparty_id', 'legal_entity_id', 'number', 'signed_on', 'starts_on',
                    'ends_on', 'status', 'subject', 'note', 'created_at', 'updated_at')
_CONTRACT_SQL = """
    SELECT %s, cp.name, le.name
      FROM payment_contracts c
      LEFT JOIN payment_counterparties cp ON cp.id = c.counterparty_id
      LEFT JOIN payment_legal_entities le ON le.id = c.legal_entity_id
""" % ', '.join('c.%s' % f for f in _CONTRACT_FIELDS)


def _contract_row(row):
    item = _map(_CONTRACT_FIELDS, row[:len(_CONTRACT_FIELDS)])
    item['counterparty_name'] = row[len(_CONTRACT_FIELDS)]
    item['legal_entity_name'] = row[len(_CONTRACT_FIELDS) + 1]
    return item


def list_contracts(cursor, counterparty_id=None):
    where, params = '', []
    if counterparty_id:
        where = 'WHERE c.counterparty_id = %s'
        params.append(int(counterparty_id))
    cursor.execute(_CONTRACT_SQL + where + " ORDER BY (c.status = 'active') DESC, c.ends_on DESC NULLS FIRST, c.id DESC",
                   params)
    return [_contract_row(row) for row in cursor.fetchall()]


def read_contract(cursor, contract_id):
    if not contract_id:
        return None
    cursor.execute(_CONTRACT_SQL + ' WHERE c.id = %s', (int(contract_id),))
    row = cursor.fetchone()
    return _contract_row(row) if row else None


def upsert_contract(cursor, *, contract_id=None, fields, actor_id=None):
    cols = ('counterparty_id', 'legal_entity_id', 'number', 'signed_on', 'starts_on', 'ends_on',
            'status', 'subject', 'note')
    values = [fields.get(col) for col in cols]
    if contract_id:
        cursor.execute(
            "UPDATE payment_contracts SET %s, updated_at = %s WHERE id = %%s RETURNING id"
            % (', '.join('%s = %%s' % col for col in cols), _NOW),
            values + [int(contract_id)])
    else:
        cursor.execute(
            "INSERT INTO payment_contracts (%s, created_by) VALUES (%s, %%s) RETURNING id"
            % (', '.join(cols), ', '.join(['%s'] * len(cols))),
            values + [actor_id])
    row = cursor.fetchone()
    return row[0] if row else None


_ORDER_FIELDS = ('id', 'number', 'issued_on', 'starts_on', 'ends_on', 'status', 'replaces_role',
                 'delegate_user_id', 'amount_limit', 'all_projects', 'all_counterparties', 'note',
                 'created_at', 'updated_at')
_ORDER_SQL = """
    SELECT %s, u.name,
           COALESCE((SELECT array_agg(op.project_id ORDER BY op.project_id)
                       FROM payment_order_projects op WHERE op.order_id = o.id), '{}'),
           COALESCE((SELECT array_agg(oc.counterparty_id ORDER BY oc.counterparty_id)
                       FROM payment_order_counterparties oc WHERE oc.order_id = o.id), '{}')
      FROM payment_approval_orders o
      LEFT JOIN users u ON u.id = o.delegate_user_id
""" % ', '.join('o.%s' % f for f in _ORDER_FIELDS)


def _order_row(row):
    item = _map(_ORDER_FIELDS, row[:len(_ORDER_FIELDS)])
    item['delegate_name'] = row[len(_ORDER_FIELDS)]
    item['project_ids'] = list(row[len(_ORDER_FIELDS) + 1] or [])
    item['counterparty_ids'] = list(row[len(_ORDER_FIELDS) + 2] or [])
    return item


def list_orders(cursor, only_active=False):
    where = " WHERE o.status = 'active'" if only_active else ''
    cursor.execute(_ORDER_SQL + where + " ORDER BY (o.status = 'active') DESC, o.issued_on DESC, o.id DESC")
    return [_order_row(row) for row in cursor.fetchall()]


def read_order(cursor, order_id):
    cursor.execute(_ORDER_SQL + ' WHERE o.id = %s', (int(order_id),))
    row = cursor.fetchone()
    return _order_row(row) if row else None


def upsert_order(cursor, *, order_id=None, fields, project_ids, counterparty_ids, actor_id=None):
    cols = ('number', 'issued_on', 'starts_on', 'ends_on', 'status', 'replaces_role',
            'delegate_user_id', 'amount_limit', 'all_projects', 'all_counterparties', 'note')
    values = [fields.get(col) for col in cols]
    if order_id:
        cursor.execute(
            "UPDATE payment_approval_orders SET %s, updated_at = %s WHERE id = %%s RETURNING id"
            % (', '.join('%s = %%s' % col for col in cols), _NOW),
            values + [int(order_id)])
    else:
        cursor.execute(
            "INSERT INTO payment_approval_orders (%s, created_by) VALUES (%s, %%s) RETURNING id"
            % (', '.join(cols), ', '.join(['%s'] * len(cols))),
            values + [actor_id])
    order_id = cursor.fetchone()[0]
    cursor.execute("DELETE FROM payment_order_projects WHERE order_id = %s", (order_id,))
    for project_id in sorted({int(x) for x in project_ids or []}):
        cursor.execute("INSERT INTO payment_order_projects (order_id, project_id) VALUES (%s, %s) "
                       "ON CONFLICT DO NOTHING", (order_id, project_id))
    cursor.execute("DELETE FROM payment_order_counterparties WHERE order_id = %s", (order_id,))
    for cp_id in sorted({int(x) for x in counterparty_ids or []}):
        cursor.execute("INSERT INTO payment_order_counterparties (order_id, counterparty_id) VALUES (%s, %s) "
                       "ON CONFLICT DO NOTHING", (order_id, cp_id))
    return order_id


def delete_dictionary_row(cursor, table, row_id):
    """Удаление строки справочника. Таблица — только из белого списка."""
    allowed = {
        'projects': 'payment_projects', 'categories': 'payment_categories',
        'legal_entities': 'payment_legal_entities', 'counterparties': 'payment_counterparties',
        'contracts': 'payment_contracts', 'orders': 'payment_approval_orders',
    }
    cursor.execute("DELETE FROM %s WHERE id = %%s" % allowed[table], (int(row_id),))
    return cursor.rowcount


# ─────────────────────────────────────────────────────────────────────────────
# Заявка
# ─────────────────────────────────────────────────────────────────────────────

_REQUEST_FIELDS = (
    'id', 'created_at', 'updated_at',
    'initiator_id', 'initiator_name', 'manager_id', 'manager_name', 'department_id', 'department_name',
    'project_id', 'branch', 'expense_name', 'category_id', 'subcategory_id', 'counterparty_id',
    'legal_entity_id', 'contract_id',
    'amount', 'currency', 'payment_period', 'payment_source', 'payment_type', 'card_number', 'notes',
    'due_on',
    'invoice_requisites', 'invoice_number', 'invoice_date', 'invoice_description',
    'needs_power_of_attorney', 'needs_payment_order', 'previous_payment_note',
    'paid_on', 'paid_amount', 'refund_on', 'refund_amount',
    'fixed_template_id',
    'current_step', 'status', 'block_code', 'block_reason', 'current_role_code',
    'current_assignee_id', 'current_assignee_name', 'approver_user_id', 'approval_order_id',
    'route_basis', 'step_changed_at', 'closed_at', 'rejected_reason',
)
_REQUEST_EXTRA = ('project_name', 'category_name', 'subcategory_name', 'counterparty_name',
                  'counterparty_bin', 'legal_entity_name', 'contract_number', 'approval_order_number',
                  'attachments_count')
_REQUEST_SQL = """
    SELECT %s,
           p.name, c1.name, c2.name, cp.name, cp.bin, le.name, ct.number, ao.number,
           (SELECT COUNT(*) FROM payment_attachments a WHERE a.request_id = r.id)
      FROM payment_requests r
      LEFT JOIN payment_projects p ON p.id = r.project_id
      LEFT JOIN payment_categories c1 ON c1.id = r.category_id
      LEFT JOIN payment_categories c2 ON c2.id = r.subcategory_id
      LEFT JOIN payment_counterparties cp ON cp.id = r.counterparty_id
      LEFT JOIN payment_legal_entities le ON le.id = r.legal_entity_id
      LEFT JOIN payment_contracts ct ON ct.id = r.contract_id
      LEFT JOIN payment_approval_orders ao ON ao.id = r.approval_order_id
""" % ', '.join('r.%s' % f for f in _REQUEST_FIELDS)

# Поля заявки, которые заполняет инициатор в форме. Служебные (шаг, статус,
# ответственный) сюда не входят — их двигает только маршрут.
EDITABLE_FIELDS = (
    'manager_id', 'department_id', 'project_id', 'branch', 'expense_name', 'category_id',
    'subcategory_id', 'counterparty_id', 'legal_entity_id', 'contract_id', 'payment_period',
    'payment_source', 'payment_type', 'card_number', 'notes', 'due_on',
    'invoice_number', 'invoice_date', 'invoice_description', 'needs_power_of_attorney',
    'needs_payment_order',
)
# Поля шагов бухгалтерии и оплаты — их правит ответственный шага.
STEP_FIELDS = ('invoice_requisites', 'previous_payment_note', 'paid_on', 'paid_amount',
               'refund_on', 'refund_amount')


def _request_row(row):
    item = _map(_REQUEST_FIELDS, row[:len(_REQUEST_FIELDS)])
    for key, value in zip(_REQUEST_EXTRA, row[len(_REQUEST_FIELDS):]):
        item[key] = value
    item['attachments_count'] = int(item.get('attachments_count') or 0)
    # В SQL колонка current_role_code: CURRENT_ROLE — зарезервированное слово Postgres.
    # Наружу ключ остаётся current_role, как его читают фронт и Telegram.
    item['current_role'] = item.get('current_role_code')
    item['state'] = workflow.request_state(item, today_almaty())
    return item


def read_request(cursor, request_id, *, lock=False):
    cursor.execute(_REQUEST_SQL + ' WHERE r.id = %s' + (' FOR UPDATE OF r' if lock else ''),
                   (int(request_id),))
    row = cursor.fetchone()
    return _request_row(row) if row else None


_SEARCH_SQL = """
    (r.expense_name ILIKE %(like)s
     OR COALESCE(r.notes, '') ILIKE %(like)s
     OR COALESCE(r.invoice_number, '') ILIKE %(like)s
     OR COALESCE(r.invoice_description, '') ILIKE %(like)s
     OR COALESCE(r.branch, '') ILIKE %(like)s
     OR COALESCE(cp.name, '') ILIKE %(like)s
     OR COALESCE(cp.bin, '') ILIKE %(like)s
     OR COALESCE(r.initiator_name, '') ILIKE %(like)s
     OR COALESCE(r.current_assignee_name, '') ILIKE %(like)s
     OR CAST(r.id AS TEXT) = %(q)s
     OR (%(digits)s <> '' AND regexp_replace(COALESCE(r.card_number, ''), '\\D', '', 'g') LIKE %(digits_like)s)
     OR EXISTS (SELECT 1 FROM payment_request_items i
                 WHERE i.request_id = r.id AND i.name ILIKE %(like)s))
"""


def _filter_clause(params, *, query=None, state=None, responsible_id=None, responsible_roles=(),
                   initiator_id=None, counterparty_id=None, project_id=None, payment_source=None,
                   payment_type=None, fixed_only=False, phase=None, date_from=None, date_to=None,
                   viewer_id=None, viewer_roles=()):
    clauses = []
    today = today_almaty()
    params['today'] = today
    if query:
        digits = ''.join(ch for ch in query if ch.isdigit())
        params.update({'like': '%' + query.strip() + '%', 'q': query.strip(),
                       'digits': digits, 'digits_like': '%' + digits + '%'})
        clauses.append(_SEARCH_SQL)
    if state == 'mine':
        params['viewer_id'] = int(viewer_id or 0)
        params['viewer_roles'] = sorted(viewer_roles or [])
        clauses.append("r.status = 'active' AND (r.current_assignee_id = %(viewer_id)s "
                       "OR (r.current_assignee_id IS NULL AND r.current_role_code = ANY(%(viewer_roles)s)))")
    elif state == 'active':
        clauses.append("r.status = 'active' AND r.block_code IS NULL "
                       "AND NOT (r.due_on IS NOT NULL AND r.due_on < %(today)s AND r.current_step <= 10)")
    elif state == 'blocked':
        clauses.append("r.status = 'active' AND r.block_code IS NOT NULL")
    elif state == 'overdue':
        clauses.append("r.status = 'active' AND r.block_code IS NULL AND r.due_on IS NOT NULL "
                       "AND r.due_on < %(today)s AND r.current_step <= 10")
    elif state in ('done', 'rejected', 'cancelled'):
        params['state'] = state
        clauses.append("r.status = %(state)s")
    elif state == 'closed':
        clauses.append("r.status IN ('done', 'rejected', 'cancelled')")
    elif state == 'open':
        clauses.append("r.status = 'active'")
    if responsible_id:
        params['responsible_id'] = int(responsible_id)
        params['responsible_roles'] = sorted(responsible_roles or [])
        clauses.append("(r.current_assignee_id = %(responsible_id)s "
                       "OR (r.current_assignee_id IS NULL AND r.current_role_code = ANY(%(responsible_roles)s)))")
    if initiator_id:
        params['initiator_id'] = int(initiator_id)
        clauses.append("r.initiator_id = %(initiator_id)s")
    if counterparty_id:
        params['counterparty_id'] = int(counterparty_id)
        clauses.append("r.counterparty_id = %(counterparty_id)s")
    if project_id:
        params['project_id'] = int(project_id)
        clauses.append("r.project_id = %(project_id)s")
    if payment_source:
        params['payment_source'] = payment_source
        clauses.append("r.payment_source = %(payment_source)s")
    if payment_type:
        params['payment_type'] = payment_type
        clauses.append("r.payment_type = %(payment_type)s")
    if fixed_only:
        clauses.append("(r.payment_type = 'fixed' OR r.fixed_template_id IS NOT NULL)")
    if phase:
        steps = [item['no'] for item in workflow.STEPS if item['phase'] == phase]
        if steps:
            params['phase_steps'] = steps
            clauses.append("r.status = 'active' AND r.current_step = ANY(%(phase_steps)s)")
    if date_from:
        params['date_from'] = date_from
        clauses.append("r.created_at >= %(date_from)s")
    if date_to:
        params['date_to'] = date_to
        clauses.append("r.created_at < (%(date_to)s::date + INTERVAL '1 day')")
    return (' WHERE ' + ' AND '.join('(%s)' % c for c in clauses)) if clauses else ''


def list_requests(cursor, *, limit=50, offset=0, **filters):
    params = {}
    where = _filter_clause(params, **filters)
    sql = _REQUEST_SQL.replace('SELECT ', 'SELECT COUNT(*) OVER () AS total, ', 1) + where
    sql += " ORDER BY (r.status = 'active') DESC, r.id DESC"
    if limit:
        params['limit'] = int(limit)
        params['offset'] = int(offset or 0)
        sql += " LIMIT %(limit)s OFFSET %(offset)s"
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    total = int(rows[0][0]) if rows else 0
    return total, [_request_row(row[1:]) for row in rows]


def state_counters(cursor, *, viewer_id=None, viewer_roles=(), **filters):
    """Счётчики полосы-легенды по ТЕКУЩИМ фильтрам без учёта состояния."""
    params = {}
    filters.pop('state', None)
    where = _filter_clause(params, viewer_id=viewer_id, viewer_roles=viewer_roles, **filters)
    params['viewer_id'] = int(viewer_id or 0)
    params['viewer_roles'] = sorted(viewer_roles or [])
    cursor.execute(
        """
        SELECT COUNT(*) AS all_count,
               COUNT(*) FILTER (WHERE r.status = 'active') AS open_count,
               COUNT(*) FILTER (WHERE r.status = 'active' AND (r.current_assignee_id = %(viewer_id)s
                        OR (r.current_assignee_id IS NULL AND r.current_role_code = ANY(%(viewer_roles)s)))) AS mine,
               COUNT(*) FILTER (WHERE r.status = 'active' AND r.block_code IS NOT NULL) AS blocked,
               COUNT(*) FILTER (WHERE r.status = 'active' AND r.block_code IS NULL AND r.due_on IS NOT NULL
                        AND r.due_on < %(today)s AND r.current_step <= 10) AS overdue,
               COUNT(*) FILTER (WHERE r.status = 'done') AS done,
               COUNT(*) FILTER (WHERE r.status IN ('rejected', 'cancelled')) AS rejected
          FROM payment_requests r
          LEFT JOIN payment_counterparties cp ON cp.id = r.counterparty_id
        """ + where,
        params,
    )
    row = cursor.fetchone() or (0,) * 7
    return {'all': int(row[0] or 0), 'open': int(row[1] or 0), 'mine': int(row[2] or 0),
            'blocked': int(row[3] or 0), 'overdue': int(row[4] or 0), 'done': int(row[5] or 0),
            'rejected': int(row[6] or 0)}


# ── Позиции ──────────────────────────────────────────────────────────────────

_ITEM_FIELDS = ('id', 'position', 'name', 'quantity', 'unit', 'unit_price')


def list_items(cursor, request_id):
    cursor.execute(
        "SELECT %s FROM payment_request_items WHERE request_id = %%s ORDER BY position, id"
        % ', '.join(_ITEM_FIELDS), (int(request_id),))
    items = [_map(_ITEM_FIELDS, row) for row in cursor.fetchall()]
    for item in items:
        item['total'] = (Decimal(item['quantity'] or 0) * Decimal(item['unit_price'] or 0)).quantize(Decimal('0.01'))
    return items


def items_for_requests(cursor, request_ids):
    """{request_id: [позиции]} одним запросом — для выгрузки."""
    ids = sorted({int(x) for x in request_ids if x})
    if not ids:
        return {}
    cursor.execute(
        "SELECT request_id, %s FROM payment_request_items WHERE request_id = ANY(%%s) "
        "ORDER BY request_id, position, id" % ', '.join(_ITEM_FIELDS), (ids,))
    result = {}
    for row in cursor.fetchall():
        result.setdefault(row[0], []).append(_map(_ITEM_FIELDS, row[1:]))
    return result


def replace_items(cursor, request_id, items):
    cursor.execute("DELETE FROM payment_request_items WHERE request_id = %s", (int(request_id),))
    for position, item in enumerate(items or []):
        cursor.execute(
            "INSERT INTO payment_request_items (request_id, position, name, quantity, unit, unit_price) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (int(request_id), position, item['name'], workflow.to_decimal(item.get('quantity'), Decimal('1')),
             item.get('unit'), workflow.to_decimal(item.get('unit_price'))))
    total = workflow.items_total(items)
    cursor.execute("UPDATE payment_requests SET amount = %s WHERE id = %s", (total, int(request_id)))
    return total


# ── Шаги ─────────────────────────────────────────────────────────────────────

_STEP_FIELDS = ('id', 'request_id', 'step_no', 'role_code', 'assignee_id', 'assignee_name', 'state',
                'done_at', 'done_by', 'done_by_name', 'comment')


def list_steps(cursor, request_id):
    cursor.execute(
        "SELECT %s FROM payment_request_steps WHERE request_id = %%s ORDER BY step_no"
        % ', '.join(_STEP_FIELDS), (int(request_id),))
    steps = []
    for row in cursor.fetchall():
        item = _map(_STEP_FIELDS, row)
        definition = workflow.step(item['step_no']) or {}
        item['title'] = definition.get('title')
        item['brief'] = definition.get('brief')
        item['phase'] = definition.get('phase')
        item['action'] = definition.get('action')
        item['role_label'] = workflow.ROLE_LABELS.get(item['role_code'], item['role_code'])
        item['files'] = list(definition.get('files') or [])
        item['files_required'] = bool(definition.get('files_required'))
        item['fields'] = list(definition.get('fields') or [])
        item['can_reject'] = bool(definition.get('can_reject'))
        item['returns_to'] = definition.get('returns_to')
        steps.append(item)
    return steps


def _write_route(cursor, request_id, route):
    for entry in route:
        cursor.execute(
            """
            INSERT INTO payment_request_steps (request_id, step_no, role_code, assignee_id, assignee_name,
                                               state, comment)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (request_id, step_no) DO UPDATE
               SET role_code = EXCLUDED.role_code, assignee_id = EXCLUDED.assignee_id,
                   assignee_name = EXCLUDED.assignee_name, state = EXCLUDED.state,
                   comment = EXCLUDED.comment
            """,
            (int(request_id), entry['step_no'], entry['role_code'], entry.get('assignee_id'),
             entry.get('assignee_name'), entry.get('state', 'pending'), entry.get('comment')))


def _set_current(cursor, request_id, step_no):
    """Делает шаг текущим и переписывает денормализованного ответственного заявки."""
    cursor.execute(
        "UPDATE payment_request_steps SET state = 'current' WHERE request_id = %s AND step_no = %s "
        "RETURNING role_code, assignee_id, assignee_name",
        (int(request_id), int(step_no)))
    row = cursor.fetchone()
    role_code, assignee_id, assignee_name = row if row else (None, None, None)
    cursor.execute(
        "UPDATE payment_requests SET current_step = %%s, current_role_code = %%s, current_assignee_id = %%s, "
        "current_assignee_name = %%s, step_changed_at = %s, updated_at = %s WHERE id = %%s"
        % (_NOW, _NOW),
        (int(step_no), role_code, assignee_id,
         assignee_name or workflow.ROLE_LABELS.get(role_code), int(request_id)))


def log_event(cursor, request_id, kind, actor, *, step_no=None, comment=None, payload=None):
    cursor.execute(
        "INSERT INTO payment_events (request_id, kind, step_no, actor_id, actor_name, comment, payload) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (int(request_id), kind, step_no, (actor or {}).get('id'), (actor or {}).get('name'),
         comment, Json(plain(payload)) if payload is not None else None))


_EVENT_FIELDS = ('id', 'kind', 'step_no', 'actor_id', 'actor_name', 'created_at', 'comment', 'payload')


def list_events(cursor, request_id):
    cursor.execute(
        "SELECT %s FROM payment_events WHERE request_id = %%s ORDER BY id" % ', '.join(_EVENT_FIELDS),
        (int(request_id),))
    return [_map(_EVENT_FIELDS, row) for row in cursor.fetchall()]


def create_request(cursor, *, fields, items, actor, initiator, manager, route, first_step_done=False,
                   system_comment=None):
    """Заводит заявку, позиции и маршрут. Возвращает id.

    first_step_done — заявка создана календарём фиксированных платежей: шаг 1
    (документы к закупу) ей не нужен — это уже согласованный ранее регулярный
    платёж, — поэтому она сразу ждёт руководителя или Учредителя.
    """
    cols = ['initiator_id', 'initiator_name', 'manager_id', 'manager_name'] + [
        col for col in EDITABLE_FIELDS if col not in ('manager_id',)]
    values = {
        'initiator_id': initiator.get('id'), 'initiator_name': initiator.get('name'),
        'manager_id': (manager or {}).get('id'), 'manager_name': (manager or {}).get('name'),
    }
    for col in EDITABLE_FIELDS:
        if col == 'manager_id':
            continue
        values[col] = fields.get(col)
    if fields.get('department_name'):
        cols.append('department_name')
        values['department_name'] = fields['department_name']
    if fields.get('fixed_template_id'):
        cols.append('fixed_template_id')
        values['fixed_template_id'] = fields['fixed_template_id']
    for flag in ('needs_power_of_attorney', 'needs_payment_order'):
        values[flag] = bool(values.get(flag))
    cursor.execute(
        "INSERT INTO payment_requests (%s) VALUES (%s) RETURNING id"
        % (', '.join(cols), ', '.join(['%%(%s)s' % col for col in cols])),
        values)
    request_id = cursor.fetchone()[0]
    replace_items(cursor, request_id, items)
    _write_route(cursor, request_id, route)
    log_event(cursor, request_id, 'created', actor, step_no=1, comment=system_comment)
    if first_step_done:
        cursor.execute(
            "UPDATE payment_request_steps SET state = 'done', done_at = %s, done_by = %%s, done_by_name = %%s, "
            "comment = %%s WHERE request_id = %%s AND step_no = 1" % _NOW,
            (actor.get('id'), actor.get('name'), system_comment, request_id))
        next_no = workflow.next_open_step(route, 1)
        _set_current(cursor, request_id, next_no or 1)
    else:
        _set_current(cursor, request_id, workflow.FIRST_STEP)
    return request_id


def update_request_fields(cursor, request_id, fields):
    """Правит перечисленные поля заявки. Возвращает {поле: (было, стало)}."""
    if not fields:
        return {}
    cols = [col for col in fields if col in EDITABLE_FIELDS + STEP_FIELDS + ('department_name',)]
    if not cols:
        return {}
    cursor.execute("SELECT %s FROM payment_requests WHERE id = %%s" % ', '.join(cols), (int(request_id),))
    before = dict(zip(cols, cursor.fetchone() or ()))
    cursor.execute(
        "UPDATE payment_requests SET %s, updated_at = %s WHERE id = %%s"
        % (', '.join('%s = %%s' % col for col in cols), _NOW),
        [fields[col] for col in cols] + [int(request_id)])
    changes = {}
    for col in cols:
        old, new = before.get(col), fields[col]
        if plain(old) != plain(new) and not (old in (None, '') and new in (None, '')):
            changes[col] = (plain(old), plain(new))
    return changes


def complete_step(cursor, request_id, step_no, actor, comment=None):
    """Отписка на шаге: шаг закрыт, следующий незапропущенный открыт.
    Возвращает номер следующего шага или None, если маршрут пройден."""
    cursor.execute(
        "UPDATE payment_request_steps SET state = 'done', done_at = %s, done_by = %%s, done_by_name = %%s, "
        "comment = %%s WHERE request_id = %%s AND step_no = %%s" % _NOW,
        (actor.get('id'), actor.get('name'), comment, int(request_id), int(step_no)))
    cursor.execute(
        "SELECT step_no, state FROM payment_request_steps WHERE request_id = %s ORDER BY step_no",
        (int(request_id),))
    route = [{'step_no': row[0], 'state': row[1]} for row in cursor.fetchall()]
    next_no = workflow.next_open_step(route, int(step_no))
    log_event(cursor, request_id, 'step_done', actor, step_no=int(step_no), comment=comment)
    if next_no is None:
        cursor.execute(
            "UPDATE payment_requests SET status = 'done', closed_at = %s, updated_at = %s, current_role_code = NULL, "
            "current_assignee_id = NULL, current_assignee_name = NULL, current_step = %%s WHERE id = %%s"
            % (_NOW, _NOW),
            (int(step_no), int(request_id)))
        return None
    _set_current(cursor, request_id, next_no)
    return next_no


def return_to_step(cursor, request_id, to_step, actor, comment):
    """Возврат на доработку: шаги от целевого до текущего снова ждут, целевой — текущий."""
    cursor.execute(
        "UPDATE payment_request_steps SET state = 'pending', done_at = NULL, done_by = NULL, done_by_name = NULL "
        "WHERE request_id = %s AND step_no >= %s AND state IN ('done', 'current')",
        (int(request_id), int(to_step)))
    _set_current(cursor, request_id, to_step)
    log_event(cursor, request_id, 'returned', actor, step_no=int(to_step), comment=comment)


def close_request(cursor, request_id, status, actor, comment):
    cursor.execute(
        "UPDATE payment_requests SET status = %%s, rejected_reason = %%s, closed_at = %s, updated_at = %s "
        "WHERE id = %%s" % (_NOW, _NOW),
        (status, comment, int(request_id)))
    log_event(cursor, request_id, status, actor, comment=comment)


def set_block(cursor, request_id, code, reason, actor):
    cursor.execute(
        "UPDATE payment_requests SET block_code = %%s, block_reason = %%s, updated_at = %s WHERE id = %%s" % _NOW,
        (code, reason, int(request_id)))
    log_event(cursor, request_id, 'blocked', actor, comment=reason, payload={'code': code})


def clear_block(cursor, request_id, actor):
    cursor.execute(
        "UPDATE payment_requests SET block_code = NULL, block_reason = NULL, updated_at = %s "
        "WHERE id = %%s AND block_code IS NOT NULL" % _NOW,
        (int(request_id),))
    if cursor.rowcount:
        log_event(cursor, request_id, 'unblocked', actor)


def set_step_assignee(cursor, request_id, step_no, user, actor, *, reason=None, kind='reassigned'):
    """Переназначить ответственного шага (None — вернуть шаг роли)."""
    cursor.execute(
        "UPDATE payment_request_steps SET assignee_id = %s, assignee_name = %s WHERE request_id = %s AND step_no = %s "
        "RETURNING state, role_code",
        ((user or {}).get('id'), (user or {}).get('name'), int(request_id), int(step_no)))
    row = cursor.fetchone()
    if row and row[0] == 'current':
        cursor.execute(
            "UPDATE payment_requests SET current_assignee_id = %s, current_assignee_name = %s WHERE id = %s",
            ((user or {}).get('id'), (user or {}).get('name') or workflow.ROLE_LABELS.get(row[1]),
             int(request_id)))
    log_event(cursor, request_id, kind, actor, step_no=int(step_no), comment=reason,
              payload={'assignee_id': (user or {}).get('id'), 'assignee_name': (user or {}).get('name')})


def set_route_basis(cursor, request_id, basis):
    cursor.execute(
        "UPDATE payment_requests SET route_basis = %s, approver_user_id = %s, approval_order_id = %s WHERE id = %s",
        (Json(plain(basis)) if basis is not None else None, (basis or {}).get('approver_user_id'),
         (basis or {}).get('order_id'), int(request_id)))


def delete_request(cursor, request_id):
    cursor.execute("SELECT bucket, blob_path FROM payment_attachments WHERE request_id = %s", (int(request_id),))
    refs = [(row[0], row[1]) for row in cursor.fetchall()]
    cursor.execute("DELETE FROM payment_requests WHERE id = %s", (int(request_id),))
    return refs


# ── Вложения ─────────────────────────────────────────────────────────────────

_ATTACHMENT_FIELDS = ('id', 'request_id', 'step_no', 'kind', 'file_name', 'content_type', 'file_size',
                      'bucket', 'blob_path', 'uploaded_by', 'uploaded_by_name', 'uploaded_at')


def list_attachments(cursor, request_id):
    cursor.execute(
        "SELECT %s FROM payment_attachments WHERE request_id = %%s ORDER BY step_no NULLS LAST, id"
        % ', '.join(_ATTACHMENT_FIELDS), (int(request_id),))
    items = []
    for row in cursor.fetchall():
        item = _map(_ATTACHMENT_FIELDS, row)
        item['kind_label'] = workflow.ATTACHMENT_LABELS.get(item['kind'], item['kind'])
        items.append(item)
    return items


def public_attachment(item):
    """Без bucket/blob_path: фронту они не нужны, а в ответе служили бы подсказкой."""
    return {key: value for key, value in item.items() if key not in ('bucket', 'blob_path')}


def add_attachment(cursor, request_id, *, step_no, kind, file_name, content_type, file_size, bucket,
                   blob_path, actor):
    cursor.execute(
        """
        INSERT INTO payment_attachments (request_id, step_no, kind, file_name, content_type, file_size,
                                         bucket, blob_path, uploaded_by, uploaded_by_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (int(request_id), step_no, kind, file_name, content_type, int(file_size or 0), bucket, blob_path,
         actor.get('id'), actor.get('name')))
    attachment_id = cursor.fetchone()[0]
    log_event(cursor, request_id, 'attachment_added', actor, step_no=step_no,
              payload={'file_name': file_name, 'kind': kind, 'attachment_id': attachment_id})
    return attachment_id


def read_attachment(cursor, attachment_id):
    cursor.execute(
        "SELECT %s FROM payment_attachments WHERE id = %%s" % ', '.join(_ATTACHMENT_FIELDS),
        (int(attachment_id),))
    row = cursor.fetchone()
    return _map(_ATTACHMENT_FIELDS, row) if row else None


def remove_attachment(cursor, attachment_id, actor):
    item = read_attachment(cursor, attachment_id)
    if not item:
        return None
    cursor.execute("DELETE FROM payment_attachments WHERE id = %s", (int(attachment_id),))
    log_event(cursor, item['request_id'], 'attachment_removed', actor, step_no=item['step_no'],
              payload={'file_name': item['file_name'], 'kind': item['kind']})
    return item


# ─────────────────────────────────────────────────────────────────────────────
# Справка «История оплат»
# ─────────────────────────────────────────────────────────────────────────────

def payment_history(cursor, *, counterparty_id=None, category_id=None, subcategory_id=None,
                    expense_name=None, exclude_request_id=None, limit=5):
    """Ранее ОПЛАЧЕННЫЕ заявки, похожие на заводимую: по контрагенту, по
    категории/подкатегории, по названию расхода. У каждой — дата последней
    оплаты, сумма и цена за единицу первой позиции (п. 9 дополнения)."""
    clauses, params = [], {'limit': int(limit)}
    if counterparty_id:
        params['cp'] = int(counterparty_id)
        clauses.append("r.counterparty_id = %(cp)s")
    if subcategory_id:
        params['sub'] = int(subcategory_id)
        clauses.append("r.subcategory_id = %(sub)s")
    elif category_id:
        params['cat'] = int(category_id)
        clauses.append("r.category_id = %(cat)s")
    name = str(expense_name or '').strip()
    if len(name) >= 3:
        params['name_like'] = '%' + name + '%'
        clauses.append("r.expense_name ILIKE %(name_like)s")
    if not clauses:
        return []
    where = ' AND (' + ' OR '.join(clauses) + ')'
    if exclude_request_id:
        params['exclude'] = int(exclude_request_id)
        where += ' AND r.id <> %(exclude)s'
    cursor.execute(
        """
        SELECT r.id, r.expense_name, r.paid_on, r.paid_amount, r.amount, r.counterparty_id, cp.name,
               r.category_id, r.subcategory_id,
               (SELECT i.unit_price FROM payment_request_items i WHERE i.request_id = r.id
                 ORDER BY i.position, i.id LIMIT 1),
               (SELECT i.name FROM payment_request_items i WHERE i.request_id = r.id
                 ORDER BY i.position, i.id LIMIT 1),
               (SELECT i.quantity FROM payment_request_items i WHERE i.request_id = r.id
                 ORDER BY i.position, i.id LIMIT 1)
          FROM payment_requests r
          LEFT JOIN payment_counterparties cp ON cp.id = r.counterparty_id
         WHERE r.paid_on IS NOT NULL AND r.status IN ('active', 'done')
        """ + where + " ORDER BY r.paid_on DESC, r.id DESC LIMIT %(limit)s",
        params)
    rows = cursor.fetchall()
    result = []
    for row in rows:
        matched = []
        if counterparty_id and row[5] == int(counterparty_id):
            matched.append('counterparty')
        if subcategory_id and row[8] == int(subcategory_id):
            matched.append('subcategory')
        elif category_id and row[7] == int(category_id):
            matched.append('category')
        if len(name) >= 3 and name.lower() in str(row[1] or '').lower():
            matched.append('name')
        result.append({
            'request_id': row[0], 'expense_name': row[1], 'paid_on': row[2], 'paid_amount': row[3],
            'amount': row[4], 'counterparty_name': row[6], 'unit_price': row[9], 'first_item': row[10],
            'quantity': row[11], 'matched': matched,
        })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Календарь фиксированных платежей
# ─────────────────────────────────────────────────────────────────────────────

_TEMPLATE_FIELDS = ('id', 'name', 'amount', 'periodicity', 'interval_days', 'next_due_on', 'lead_days',
                    'project_id', 'branch', 'responsible_user_id', 'category_id', 'subcategory_id',
                    'counterparty_id', 'legal_entity_id', 'payment_source', 'note', 'is_active',
                    'last_generated_on', 'created_at', 'updated_at')
_TEMPLATE_EXTRA = ('responsible_name', 'project_name', 'category_name', 'subcategory_name',
                   'counterparty_name', 'legal_entity_name', 'requests_count', 'open_request_id')
_TEMPLATE_SQL = """
    SELECT %s, u.name, p.name, c1.name, c2.name, cp.name, le.name,
           (SELECT COUNT(*) FROM payment_requests r WHERE r.fixed_template_id = t.id),
           (SELECT r.id FROM payment_requests r WHERE r.fixed_template_id = t.id AND r.status = 'active'
             ORDER BY r.id DESC LIMIT 1)
      FROM payment_fixed_templates t
      LEFT JOIN users u ON u.id = t.responsible_user_id
      LEFT JOIN payment_projects p ON p.id = t.project_id
      LEFT JOIN payment_categories c1 ON c1.id = t.category_id
      LEFT JOIN payment_categories c2 ON c2.id = t.subcategory_id
      LEFT JOIN payment_counterparties cp ON cp.id = t.counterparty_id
      LEFT JOIN payment_legal_entities le ON le.id = t.legal_entity_id
""" % ', '.join('t.%s' % f for f in _TEMPLATE_FIELDS)


def _template_row(row):
    item = _map(_TEMPLATE_FIELDS, row[:len(_TEMPLATE_FIELDS)])
    for key, value in zip(_TEMPLATE_EXTRA, row[len(_TEMPLATE_FIELDS):]):
        item[key] = value
    return item


def list_templates(cursor, include_inactive=True):
    where = '' if include_inactive else ' WHERE t.is_active'
    cursor.execute(_TEMPLATE_SQL + where + ' ORDER BY t.is_active DESC, t.next_due_on, t.name')
    return [_template_row(row) for row in cursor.fetchall()]


def read_template(cursor, template_id, *, lock=False):
    cursor.execute(_TEMPLATE_SQL + ' WHERE t.id = %s' + (' FOR UPDATE OF t' if lock else ''),
                   (int(template_id),))
    row = cursor.fetchone()
    return _template_row(row) if row else None


def upsert_template(cursor, *, template_id=None, fields, actor_id=None):
    cols = ('name', 'amount', 'periodicity', 'interval_days', 'next_due_on', 'lead_days', 'project_id',
            'branch', 'responsible_user_id', 'category_id', 'subcategory_id', 'counterparty_id',
            'legal_entity_id', 'payment_source', 'note', 'is_active')
    values = [fields.get(col) for col in cols]
    if template_id:
        cursor.execute(
            "UPDATE payment_fixed_templates SET %s, updated_at = %s WHERE id = %%s RETURNING id"
            % (', '.join('%s = %%s' % col for col in cols), _NOW),
            values + [int(template_id)])
    else:
        cursor.execute(
            "INSERT INTO payment_fixed_templates (%s, created_by) VALUES (%s, %%s) RETURNING id"
            % (', '.join(cols), ', '.join(['%s'] * len(cols))),
            values + [actor_id])
    row = cursor.fetchone()
    return row[0] if row else None


def advance_template(cursor, template_id, *, next_due_on, generated_on):
    cursor.execute(
        "UPDATE payment_fixed_templates SET next_due_on = %%s, last_generated_on = %%s, updated_at = %s "
        "WHERE id = %%s" % _NOW,
        (next_due_on, generated_on, int(template_id)))


def delete_template(cursor, template_id):
    cursor.execute("DELETE FROM payment_fixed_templates WHERE id = %s", (int(template_id),))
    return cursor.rowcount


def department_brief(cursor, department_id):
    if not department_id:
        return None
    cursor.execute("SELECT id, name FROM departments WHERE id = %s", (int(department_id),))
    row = cursor.fetchone()
    return {'id': row[0], 'name': row[1]} if row else None


def list_departments(cursor):
    cursor.execute("SELECT id, name FROM departments WHERE is_active ORDER BY name")
    return [{'id': row[0], 'name': row[1]} for row in cursor.fetchall()]
