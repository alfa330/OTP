"""SQL заявок раздела «Оплата счетов»: заявка, позиции, поставщики, подзадачи,
история, вложения, реестр, доски. Только запросы и разбор строк, без Flask.

Справочники — в directory.py, имущество — в assets.py, уведомления — в
notices.py; общие мелочи (время, plain, разбор строки) — в sqlutil.py.

Маршрут материализован: подзадачи заявки лежат строками `payment_subtasks`,
дальше запросы двигают состояние по ним, а не пересчитывают. Решения «что
дальше» принимает flow.py по правилам workflow.py; здесь только запись.

Номер карты наружу отсюда не выходит: в полях заявки только последние четыре
цифры, полный номер читает `card_number()` — её зовёт одна ручка.
"""

from psycopg2.extras import Json

from . import cards, workflow
from .sqlutil import NOW_SQL as _NOW, columns, like_pattern, now_almaty, plain, row_map as _map, today_almaty

__all__ = ['now_almaty', 'today_almaty', 'plain']


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
    """Сотрудники для выбора участников ролей, согласующих и ответственных.
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
    """{'approver': [{user_id, name, has_telegram, department_name}], 'accounting': [...], …}"""
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


def role_backlog(cursor, role_code):
    """Сколько живых заявок ещё ждут эту роль: её подзадача не выполнена и не назначена лично."""
    cursor.execute(
        "SELECT COUNT(DISTINCT s.request_id) FROM payment_subtasks s "
        "JOIN payment_requests r ON r.id = s.request_id "
        "WHERE r.status = 'active' AND r.submitted_at IS NOT NULL AND s.role_code = %s "
        "AND s.assignee_id IS NULL AND s.state IN ('pending', 'open', 'waiting')",
        (role_code,))
    return int(cursor.fetchone()[0] or 0)


def user_brief(cursor, user_id):
    if not user_id:
        return None
    cursor.execute(
        """
        SELECT u.id, u.name, u.telegram_id, u.department_id, d.name, u.status
          FROM users u LEFT JOIN departments d ON d.id = u.department_id
         WHERE u.id = %s
        """,
        (int(user_id),),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': row[0], 'name': row[1], 'telegram_id': row[2],
            'department_id': row[3], 'department_name': row[4], 'fired': row[5] == 'fired'}


def resolve_manager(cursor, user_id):
    """Непосредственный руководитель: users.supervisor_id, иначе глава отдела.

    ТЗ, п. 4.1: руководитель «определяется автоматически» — инициатор его не
    выбирает (п. 6). Сам себе человек не руководитель: ни как глава своего
    отдела, ни по ошибочной записи, где он указан собственным начальником.
    Нет ни того, ни другого — None: этап руководителя будет пропущен с пометкой
    (см. workflow.build_route).
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
    if supervisor_id and supervisor_id != int(user_id) and supervisor_status != 'fired':
        return {'id': supervisor_id, 'name': supervisor_name, 'source': 'supervisor'}
    if head_id and head_id != int(user_id) and head_status != 'fired':
        return {'id': head_id, 'name': head_name, 'source': 'department_head'}
    return None


def is_manager_somewhere(cursor, user_id):
    """Есть ли у человека лично назначенные согласования — показывать ли ему доску
    согласования. Это руководитель инициатора и согласующий, которого назвала
    матрица (лимит, маршрут, поставщик), даже если роли «Утвердитель» у него нет."""
    cursor.execute(
        "SELECT EXISTS (SELECT 1 FROM payment_subtasks WHERE kind = ANY(%s) AND assignee_id = %s "
        "AND state IN ('open', 'waiting', 'done'))",
        (list(workflow.APPROVAL_KINDS), int(user_id)))
    return bool(cursor.fetchone()[0])


def telegram_recipients(cursor, *, user_ids=(), exclude=()):
    """[{user_id, name, chat_id}] — у кого из названных людей привязан Telegram."""
    ids = {int(x) for x in user_ids if x} - {int(x) for x in exclude if x}
    if not ids:
        return []
    cursor.execute(
        "SELECT id, name, telegram_id FROM users WHERE id = ANY(%s) AND telegram_id IS NOT NULL "
        "AND status <> 'fired'",
        (sorted(ids),),
    )
    return [{'user_id': row[0], 'name': row[1], 'chat_id': row[2]} for row in cursor.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Заявка
# ─────────────────────────────────────────────────────────────────────────────

_REQUEST_FIELDS = (
    'id', 'created_at', 'updated_at',
    'initiator_id', 'initiator_name', 'manager_id', 'manager_name', 'department_id', 'department_name',
    'request_kind', 'project_id', 'branch', 'expense_name', 'justification', 'category_id', 'subcategory_id',
    'counterparty_id', 'counterparty_account_id', 'legal_entity_id', 'contract_id',
    'amount', 'currency', 'payment_period', 'payment_method', 'payment_purpose',
    'card_recipient', 'card_holder_name', 'card_holder_user_id', 'card_id', 'card_last4',
    'object_type', 'accounting_category',
    'no_alternatives', 'no_alternatives_reason', 'no_alternatives_comment', 'supplier_choice_reason',
    'notes', 'due_on',
    'invoice_number', 'invoice_date', 'invoice_description',
    'paid_on', 'paid_amount', 'paid_comment', 'refund_on', 'refund_amount',
    'received_on', 'received_quantity', 'closing_docs_status', 'closing_docs_at',
    'supplier_kind', 'supplier_vat',
    'fixed_template_id',
    'stage', 'status', 'current_subtask_id', 'current_role_code', 'current_assignee_id',
    'current_assignee_name', 'approver_user_id', 'approval_order_id', 'route_basis',
    'submitted_at', 'submitted_snapshot', 'approved_at', 'approved_by', 'approved_by_name',
    'amount_approved', 'step_changed_at', 'closed_at', 'rejected_reason', 'legacy_step',
)
_REQUEST_EXTRA = ('project_name', 'category_name', 'subcategory_name', 'counterparty_name',
                  'counterparty_bin', 'counterparty_vat', 'legal_entity_name', 'contract_number',
                  'template_name', 'current_kind', 'current_status', 'current_state',
                  'clarify_reason', 'clarify_comment', 'clarify_by_name', 'clarify_from',
                  'has_card', 'attachments_count')
_REQUEST_SQL = """
    SELECT %s,
           p.name, c1.name, c2.name, cp.name, cp.bin, cp.vat_payer, le.name, ct.number, ft.name,
           cs.kind, cs.status, cs.state, cs.clarify_reason, cs.clarify_comment, cs.clarify_by_name, cs.outcome,
           (r.card_number_enc IS NOT NULL),
           (SELECT COUNT(*) FROM payment_attachments a WHERE a.request_id = r.id)
      FROM payment_requests r
      LEFT JOIN payment_projects p ON p.id = r.project_id
      LEFT JOIN payment_categories c1 ON c1.id = r.category_id
      LEFT JOIN payment_categories c2 ON c2.id = r.subcategory_id
      LEFT JOIN payment_counterparties cp ON cp.id = r.counterparty_id
      LEFT JOIN payment_legal_entities le ON le.id = r.legal_entity_id
      LEFT JOIN payment_contracts ct ON ct.id = r.contract_id
      LEFT JOIN payment_fixed_templates ft ON ft.id = r.fixed_template_id
      LEFT JOIN payment_subtasks cs ON cs.id = r.current_subtask_id
""" % columns('r', _REQUEST_FIELDS)

# Поля заявки, которые заполняет инициатор в форме. Служебные (этап, статус,
# исполнитель) сюда не входят — их двигает только маршрут; руководителя система
# определяет сама (п. 4.1).
EDITABLE_FIELDS = (
    'request_kind', 'department_id', 'project_id', 'branch', 'expense_name', 'justification',
    'category_id', 'subcategory_id', 'counterparty_id', 'counterparty_account_id', 'legal_entity_id',
    'contract_id', 'payment_period', 'payment_method', 'payment_purpose', 'card_recipient',
    'card_holder_name', 'card_holder_user_id', 'card_id', 'object_type', 'accounting_category',
    'no_alternatives', 'no_alternatives_reason', 'no_alternatives_comment', 'supplier_choice_reason',
    'notes', 'due_on', 'invoice_number', 'invoice_date', 'invoice_description', 'fixed_template_id',
)
# Поля, которые пишут действия подзадач и сам маршрут.
PROCESS_FIELDS = (
    'department_name', 'manager_id', 'manager_name', 'paid_on', 'paid_amount', 'paid_comment',
    'refund_on', 'refund_amount', 'received_on', 'received_quantity', 'closing_docs_status',
    'supplier_kind', 'supplier_vat',
)


def _request_row(row):
    item = _map(_REQUEST_FIELDS, row[:len(_REQUEST_FIELDS)])
    for key, value in zip(_REQUEST_EXTRA, row[len(_REQUEST_FIELDS):]):
        item[key] = value
    item['attachments_count'] = int(item.get('attachments_count') or 0)
    item['has_card'] = bool(item.get('has_card'))
    item['card_mask'] = cards.mask(item.get('card_last4'))
    # В SQL колонка current_role_code: CURRENT_ROLE — зарезервированное слово Postgres.
    # Наружу ключ остаётся current_role, как его читают фронт и Telegram.
    item['current_role'] = item.get('current_role_code')
    item['stage_label'] = workflow.STAGE_LABELS.get(item.get('stage'))
    item['state'] = workflow.request_state(item, today_almaty())
    return item


def read_request(cursor, request_id, *, lock=False):
    cursor.execute(_REQUEST_SQL + ' WHERE r.id = %s' + (' FOR UPDATE OF r' if lock else ''),
                   (int(request_id),))
    row = cursor.fetchone()
    return _request_row(row) if row else None


def card_number(cursor, request_id):
    """Полный номер карты заявки цифрами либо None. Зовёт одна ручка — с проверкой прав."""
    cursor.execute("SELECT card_number_enc FROM payment_requests WHERE id = %s", (int(request_id),))
    row = cursor.fetchone()
    return cards.decrypt(row[0]) if row and row[0] else None


def set_card_number(cursor, request_id, number):
    """Записывает номер карты заявки зашифрованным. Возвращает, изменился ли он."""
    previous = card_number(cursor, request_id)
    number = cards.digits(number)
    if (previous or '') == number:
        return False
    cursor.execute(
        "UPDATE payment_requests SET card_number_enc = %s, card_last4 = %s WHERE id = %s",
        (cards.encrypt(number), cards.last4(number) or None, int(request_id)))
    return True


def copy_card_from_directory(cursor, request_id, card_id):
    """Номер из справочника карт → в заявку (заявка хранит свою копию: правка
    справочника не должна менять то, что уже согласовано)."""
    cursor.execute("SELECT card_number_enc, card_last4 FROM payment_cards WHERE id = %s", (int(card_id),))
    row = cursor.fetchone()
    if not row:
        return False
    previous = card_number(cursor, request_id)
    cursor.execute(
        "UPDATE payment_requests SET card_number_enc = %s, card_last4 = %s WHERE id = %s",
        (row[0], row[1], int(request_id)))
    return (previous or '') != (cards.decrypt(row[0]) or '')


# ── Реестр и фильтры (п. 19) ─────────────────────────────────────────────────

# Поиск: по номеру заявки, номеру счёта, поставщику и назначению платежа (п. 19);
# название закупа и инициатор оставлены из первой версии — ими ищут чаще всего.
_SEARCH_SQL = """
    (CAST(r.id AS TEXT) = %(q)s
     OR COALESCE(r.invoice_number, '') ILIKE %(like)s
     OR COALESCE(cp.name, '') ILIKE %(like)s
     OR COALESCE(cp.bin, '') ILIKE %(like)s
     OR COALESCE(r.payment_purpose, '') ILIKE %(like)s
     OR r.expense_name ILIKE %(like)s
     OR COALESCE(r.initiator_name, '') ILIKE %(like)s)
"""

# Что видит человек без полного реестра: заявки, где он инициатор, руководитель
# или исполнитель (лично либо ролью).
_VISIBLE_SQL = (
    "(r.initiator_id = %(visible_to)s OR r.manager_id = %(visible_to)s OR EXISTS ("
    "SELECT 1 FROM payment_subtasks vs WHERE vs.request_id = r.id AND vs.state <> 'pending' "
    "AND vs.state <> 'skipped' AND (vs.assignee_id = %(visible_to)s OR vs.done_by = %(visible_to)s "
    "OR (vs.assignee_id IS NULL AND vs.role_code = ANY(%(visible_roles)s)))))")

_FILTER_KEYS = ('query', 'state', 'legal_entity_id', 'department_id', 'initiator_id', 'assignee_id',
                'assignee_roles', 'approver_id', 'counterparty_id', 'request_kind', 'payment_method',
                'amount_from', 'amount_to', 'date_from', 'date_to', 'overdue', 'mine',
                'viewer_id', 'viewer_roles', 'viewer_approves_own', 'visible_to', 'visible_roles')

# Заявка ждёт смотрящего: она у него лично либо у его роли. Своя заявка на
# согласовании у роли целиком ждёт не его, а остальных утверждающих
# (access.approves_own_request) — если только он не администратор раздела.
_MINE_SQL = (
    "r.status = 'active' AND (r.current_assignee_id = %(viewer_id)s "
    "OR (r.current_assignee_id IS NULL AND r.current_role_code = ANY(%(viewer_roles)s) "
    "AND (%(viewer_approves_own)s OR r.current_role_code <> 'approver' "
    "OR r.initiator_id IS DISTINCT FROM %(viewer_id)s)))")


def _filter_clause(params, *, query=None, state=None, legal_entity_id=None, department_id=None,
                   initiator_id=None, assignee_id=None, assignee_roles=(), approver_id=None,
                   counterparty_id=None, request_kind=None, payment_method=None,
                   amount_from=None, amount_to=None, date_from=None, date_to=None,
                   overdue=False, mine=False, viewer_id=None, viewer_roles=(), viewer_approves_own=False,
                   visible_to=None, visible_roles=()):
    """Условия отбора заявок. Набор фильтров — п. 19 ТЗ: компания, отдел,
    инициатор, исполнитель, согласующий, поставщик, тип заявки, способ оплаты,
    статус, сумма, дата, «просрочено», «только мои».

    `visible_to` — ограничение видимости: человек без полного реестра видит
    заявки, где он инициатор, руководитель или исполнитель (лично либо ролью).
    """
    clauses = []
    params['today'] = today_almaty()
    params['viewer_id'] = int(viewer_id or 0)
    params['viewer_roles'] = sorted(viewer_roles or [])
    params['viewer_approves_own'] = bool(viewer_approves_own)
    mine_sql = _MINE_SQL
    overdue_sql = ("r.status = 'active' AND r.due_on IS NOT NULL AND r.due_on < %(today)s "
                   "AND r.paid_on IS NULL")
    clarify_sql = "r.status = 'active' AND r.stage = 'initiation' AND r.submitted_at IS NOT NULL"
    if query:
        params.update({'like': like_pattern(query.strip()), 'q': query.strip().lstrip('№#')})
        clauses.append(_SEARCH_SQL)
    if state == 'mine' or mine:
        clauses.append(mine_sql)
    if state == 'open':
        clauses.append("r.status = 'active'")
    elif state == 'clarification':
        clauses.append(clarify_sql)
    elif state == 'overdue':
        clauses.append(overdue_sql + ' AND NOT (%s)' % clarify_sql)
    elif state in ('done', 'rejected', 'cancelled'):
        params['state'] = state
        clauses.append("r.status = %(state)s")
    elif state == 'closed':
        clauses.append("r.status IN ('done', 'rejected', 'cancelled')")
    if overdue:
        clauses.append(overdue_sql)
    for key, column, value in (
        ('legal_entity_id', 'r.legal_entity_id', legal_entity_id),
        ('department_id', 'r.department_id', department_id),
        ('initiator_id', 'r.initiator_id', initiator_id),
        ('counterparty_id', 'r.counterparty_id', counterparty_id),
    ):
        if value:
            params[key] = int(value)
            clauses.append('%s = %%(%s)s' % (column, key))
    if assignee_id:
        params['assignee_id'] = int(assignee_id)
        params['assignee_roles'] = sorted(assignee_roles or [])
        clauses.append("(r.current_assignee_id = %(assignee_id)s "
                       "OR (r.current_assignee_id IS NULL AND r.current_role_code = ANY(%(assignee_roles)s)))")
    if approver_id:
        # Согласующий — тот, кому заявка ушла на утверждение или кто её утвердил.
        params['approver_id'] = int(approver_id)
        clauses.append(
            "EXISTS (SELECT 1 FROM payment_subtasks fs WHERE fs.request_id = r.id "
            "AND fs.kind IN ('manager_approval', 'approval') "
            "AND (fs.assignee_id = %(approver_id)s OR fs.done_by = %(approver_id)s))")
    if request_kind:
        params['request_kind'] = request_kind
        clauses.append("r.request_kind = %(request_kind)s")
    if payment_method:
        params['payment_method'] = payment_method
        clauses.append("r.payment_method = %(payment_method)s")
    if amount_from not in (None, ''):
        params['amount_from'] = workflow.to_decimal(amount_from)
        clauses.append("r.amount >= %(amount_from)s")
    if amount_to not in (None, ''):
        params['amount_to'] = workflow.to_decimal(amount_to)
        clauses.append("r.amount <= %(amount_to)s")
    if date_from:
        params['date_from'] = date_from
        clauses.append("r.created_at >= %(date_from)s")
    if date_to:
        params['date_to'] = date_to
        clauses.append("r.created_at < (%(date_to)s::date + INTERVAL '1 day')")
    if visible_to:
        params['visible_to'] = int(visible_to)
        params['visible_roles'] = sorted(visible_roles or [])
        clauses.append(_VISIBLE_SQL)
    return (' WHERE ' + ' AND '.join('(%s)' % c for c in clauses)) if clauses else ''


def filters_only(filters):
    return {key: value for key, value in (filters or {}).items() if key in _FILTER_KEYS}


def list_requests(cursor, *, limit=50, offset=0, **filters):
    params = {}
    where = _filter_clause(params, **filters_only(filters))
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


def state_counters(cursor, **filters):
    """Счётчики полосы-легенды по ТЕКУЩИМ фильтрам без учёта состояния."""
    params = {}
    filters = filters_only(filters)
    filters.pop('state', None)
    filters.pop('mine', None)
    where = _filter_clause(params, **filters)
    cursor.execute(
        """
        SELECT COUNT(*) AS all_count,
               COUNT(*) FILTER (WHERE r.status = 'active') AS open_count,
               COUNT(*) FILTER (WHERE """ + _MINE_SQL + """) AS mine,
               COUNT(*) FILTER (WHERE r.status = 'active' AND r.stage = 'initiation'
                        AND r.submitted_at IS NOT NULL) AS clarification,
               COUNT(*) FILTER (WHERE r.status = 'active' AND r.due_on IS NOT NULL AND r.due_on < %(today)s
                        AND r.paid_on IS NULL
                        AND NOT (r.stage = 'initiation' AND r.submitted_at IS NOT NULL)) AS overdue,
               COUNT(*) FILTER (WHERE r.status = 'done') AS done,
               COUNT(*) FILTER (WHERE r.status = 'rejected') AS rejected,
               COUNT(*) FILTER (WHERE r.status = 'cancelled') AS cancelled
          FROM payment_requests r
          LEFT JOIN payment_counterparties cp ON cp.id = r.counterparty_id
        """ + where,
        params,
    )
    row = cursor.fetchone() or (0,) * 8
    return {'all': int(row[0] or 0), 'open': int(row[1] or 0), 'mine': int(row[2] or 0),
            'clarification': int(row[3] or 0), 'overdue': int(row[4] or 0), 'done': int(row[5] or 0),
            'rejected': int(row[6] or 0), 'cancelled': int(row[7] or 0)}


def requests_by_ids(cursor, request_ids):
    ids = sorted({int(x) for x in request_ids if x})
    if not ids:
        return []
    cursor.execute(_REQUEST_SQL + ' WHERE r.id = ANY(%s) ORDER BY r.id DESC', (ids,))
    return [_request_row(row) for row in cursor.fetchall()]


# ── Позиции ──────────────────────────────────────────────────────────────────

_ITEM_FIELDS = ('id', 'position', 'name', 'quantity', 'unit', 'unit_price')


def list_items(cursor, request_id):
    cursor.execute(
        "SELECT %s FROM payment_request_items WHERE request_id = %%s ORDER BY position, id"
        % ', '.join(_ITEM_FIELDS), (int(request_id),))
    items = [_map(_ITEM_FIELDS, row) for row in cursor.fetchall()]
    for item in items:
        item['total'] = workflow.item_total(item)
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
            (int(request_id), position, item['name'], workflow.item_quantity(item),
             item.get('unit'), workflow.item_price(item)))
    total = workflow.items_total(items)
    cursor.execute("UPDATE payment_requests SET amount = %s WHERE id = %s", (total, int(request_id)))
    return total


# ── Поставщики заявки (п. 4.2) ───────────────────────────────────────────────

_OFFER_FIELDS = ('id', 'request_id', 'position', 'counterparty_id', 'supplier_name', 'amount', 'terms',
                 'link', 'comment', 'is_recommended')


def list_offers(cursor, request_id):
    cursor.execute(
        "SELECT %s FROM payment_request_offers WHERE request_id = %%s ORDER BY position, id"
        % ', '.join(_OFFER_FIELDS), (int(request_id),))
    return [_map(_OFFER_FIELDS, row) for row in cursor.fetchall()]


def offers_for_requests(cursor, request_ids):
    ids = sorted({int(x) for x in request_ids if x})
    if not ids:
        return {}
    cursor.execute(
        "SELECT %s FROM payment_request_offers WHERE request_id = ANY(%%s) ORDER BY request_id, position, id"
        % ', '.join(_OFFER_FIELDS), (ids,))
    result = {}
    for row in cursor.fetchall():
        item = _map(_OFFER_FIELDS, row)
        result.setdefault(item['request_id'], []).append(item)
    return result


def save_offers(cursor, request_id, offers):
    """Сохраняет варианты поставщиков заявки и возвращает их id по порядку.

    Вариант с `id` правится на месте, без `id` — заводится, исчезнувший из
    списка — удаляется. На месте, а не «стереть и записать»: к варианту
    привязаны вложения (КП), и пересоздание оторвало бы их от поставщика.
    """
    existing = {item['id'] for item in list_offers(cursor, request_id)}
    kept, ids = set(), []
    for position, offer in enumerate(offers or []):
        values = (position, offer.get('counterparty_id'), offer['supplier_name'], offer.get('amount'),
                  offer.get('terms'), offer.get('link'), offer.get('comment'), bool(offer.get('is_recommended')))
        offer_id = offer.get('id')
        if offer_id and int(offer_id) in existing:
            cursor.execute(
                "UPDATE payment_request_offers SET position = %s, counterparty_id = %s, supplier_name = %s, "
                "amount = %s, terms = %s, link = %s, comment = %s, is_recommended = %s "
                "WHERE id = %s AND request_id = %s",
                values + (int(offer_id), int(request_id)))
            offer_id = int(offer_id)
        else:
            cursor.execute(
                "INSERT INTO payment_request_offers (request_id, position, counterparty_id, supplier_name, "
                "amount, terms, link, comment, is_recommended) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id",
                (int(request_id),) + values)
            offer_id = cursor.fetchone()[0]
        kept.add(offer_id)
        ids.append(offer_id)
    gone = sorted(existing - kept)
    if gone:
        cursor.execute("DELETE FROM payment_request_offers WHERE id = ANY(%s)", (gone,))
    return ids


# ── Подзадачи ────────────────────────────────────────────────────────────────

_SUBTASK_FIELDS = ('id', 'request_id', 'kind', 'position', 'stage', 'role_code', 'assignee_id',
                   'assignee_name', 'state', 'status', 'status_before', 'clarify_reason',
                   'clarify_comment', 'clarify_by', 'clarify_by_name', 'clarify_at', 'opened_at',
                   'done_at', 'done_by', 'done_by_name', 'outcome', 'comment')


def _subtask_row(row):
    item = _map(_SUBTASK_FIELDS, row)
    definition = workflow.subtask(item['kind']) or {}
    item['title'] = definition.get('title') or item['kind']
    item['brief'] = definition.get('brief')
    item['board'] = definition.get('board')
    item['stage_label'] = workflow.STAGE_LABELS.get(item['stage'])
    item['role_label'] = workflow.ROLE_LABELS.get(item['role_code'], item['role_code'])
    item['clarify_label'] = workflow.clarify_label(item['clarify_reason']) if item.get('clarify_reason') else None
    return item


def list_subtasks(cursor, request_id):
    cursor.execute(
        "SELECT %s FROM payment_subtasks WHERE request_id = %%s ORDER BY position, id"
        % ', '.join(_SUBTASK_FIELDS), (int(request_id),))
    return [_subtask_row(row) for row in cursor.fetchall()]


def read_subtask(cursor, request_id, kind):
    cursor.execute(
        "SELECT %s FROM payment_subtasks WHERE request_id = %%s AND kind = %%s" % ', '.join(_SUBTASK_FIELDS),
        (int(request_id), kind))
    row = cursor.fetchone()
    return _subtask_row(row) if row else None


def subtasks_for_requests(cursor, request_ids):
    ids = sorted({int(x) for x in request_ids if x})
    if not ids:
        return {}
    cursor.execute(
        "SELECT %s FROM payment_subtasks WHERE request_id = ANY(%%s) ORDER BY request_id, position, id"
        % ', '.join(_SUBTASK_FIELDS), (ids,))
    result = {}
    for row in cursor.fetchall():
        item = _subtask_row(row)
        result.setdefault(item['request_id'], []).append(item)
    return result


def ensure_initiation(cursor, request_id, initiator, *, status='draft'):
    """Заводит (или открывает заново) подзадачу инициатора и делает её текущей."""
    definition = workflow.SUBTASK_BY_KIND[workflow.KIND_INITIATION]
    cursor.execute(
        """
        INSERT INTO payment_subtasks (request_id, kind, position, stage, role_code, assignee_id, assignee_name,
                                      state, status, opened_at)
        VALUES (%%s, %%s, %%s, %%s, %%s, %%s, %%s, 'open', %%s, %s)
        ON CONFLICT (request_id, kind) DO UPDATE
           SET state = 'open', status = EXCLUDED.status, assignee_id = EXCLUDED.assignee_id,
               assignee_name = EXCLUDED.assignee_name, opened_at = EXCLUDED.opened_at,
               done_at = NULL, done_by = NULL, done_by_name = NULL
        RETURNING id
        """ % _NOW,
        (int(request_id), workflow.KIND_INITIATION, definition['position'], definition['stage'],
         definition['role'], (initiator or {}).get('id'), (initiator or {}).get('name'), status))
    subtask_id = cursor.fetchone()[0]
    set_current(cursor, request_id, read_subtask(cursor, request_id, workflow.KIND_INITIATION))
    return subtask_id


def write_route(cursor, request_id, route):
    """Записывает маршрут заявки: подзадачи кроме инициации сбрасываются в
    начало («не начата» либо «пропущена»). Вид, которого в маршруте нет, а в
    заявке он остался от прежнего маршрута, помечается пропущенным."""
    kinds = []
    for entry in route:
        if entry['kind'] == workflow.KIND_INITIATION:
            continue
        kinds.append(entry['kind'])
        cursor.execute(
            """
            INSERT INTO payment_subtasks (request_id, kind, position, stage, role_code, assignee_id,
                                          assignee_name, state, comment)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (request_id, kind) DO UPDATE
               SET position = EXCLUDED.position, stage = EXCLUDED.stage, role_code = EXCLUDED.role_code,
                   assignee_id = EXCLUDED.assignee_id, assignee_name = EXCLUDED.assignee_name,
                   state = EXCLUDED.state, comment = EXCLUDED.comment, status = NULL, status_before = NULL,
                   clarify_reason = NULL, clarify_comment = NULL, clarify_by = NULL, clarify_by_name = NULL,
                   clarify_at = NULL, opened_at = NULL, done_at = NULL, done_by = NULL, done_by_name = NULL,
                   outcome = NULL
            """,
            (int(request_id), entry['kind'], entry['position'], entry['stage'], entry['role_code'],
             entry.get('assignee_id'), entry.get('assignee_name'), entry.get('state', 'pending'),
             entry.get('comment')))
    cursor.execute(
        "UPDATE payment_subtasks SET state = 'skipped', status = NULL, comment = %s "
        "WHERE request_id = %s AND kind <> %s AND NOT (kind = ANY(%s))",
        ('Этап не требуется по условиям заявки', int(request_id), workflow.KIND_INITIATION, kinds))


def set_current(cursor, request_id, subtask):
    """Переписывает денормализованное «где заявка сейчас»: подзадача, этап, исполнитель."""
    subtask = subtask or {}
    cursor.execute(
        "UPDATE payment_requests SET current_subtask_id = %%s, current_role_code = %%s, "
        "current_assignee_id = %%s, current_assignee_name = %%s, stage = %%s, "
        "step_changed_at = %s, updated_at = %s WHERE id = %%s" % (_NOW, _NOW),
        (subtask.get('id'), subtask.get('role_code'), subtask.get('assignee_id'),
         subtask.get('assignee_name') or workflow.ROLE_LABELS.get(subtask.get('role_code')),
         subtask.get('stage'), int(request_id)))


def open_subtask(cursor, request_id, kind, *, status=None):
    cursor.execute(
        "UPDATE payment_subtasks SET state = 'open', status = %%s, status_before = NULL, opened_at = %s "
        "WHERE request_id = %%s AND kind = %%s RETURNING id" % _NOW,
        (status or workflow.initial_status(kind), int(request_id), kind))
    item = read_subtask(cursor, request_id, kind)
    set_current(cursor, request_id, item)
    return item


def set_subtask_status(cursor, subtask_id, status):
    cursor.execute("UPDATE payment_subtasks SET status = %s WHERE id = %s", (status, int(subtask_id)))


def wait_subtask(cursor, subtask_id, *, reason, comment=None, actor=None, status_before=None):
    """Подзадача ждёт инициатора: исполнитель запросил уточнение или вернул на доработку."""
    cursor.execute(
        "UPDATE payment_subtasks SET state = 'waiting', status_before = COALESCE(%%s, status), "
        "status = 'clarification', clarify_reason = %%s, clarify_comment = %%s, clarify_by = %%s, "
        "clarify_by_name = %%s, clarify_at = %s, opened_at = COALESCE(opened_at, %s) WHERE id = %%s"
        % (_NOW, _NOW),
        (status_before, reason, comment, (actor or {}).get('id'), (actor or {}).get('name'), int(subtask_id)))


def resume_subtask(cursor, request_id, kind):
    """Ответ инициатора получен: подзадача снова у исполнителя, в прежнем статусе."""
    cursor.execute(
        "UPDATE payment_subtasks SET state = 'open', status = COALESCE(status_before, %s), "
        "status_before = NULL, clarify_reason = NULL, clarify_comment = NULL, clarify_by = NULL, "
        "clarify_by_name = NULL, clarify_at = NULL WHERE request_id = %s AND kind = %s",
        (workflow.initial_status(kind), int(request_id), kind))
    item = read_subtask(cursor, request_id, kind)
    set_current(cursor, request_id, item)
    return item


def reopen_initiation(cursor, request_id, *, status, reason, comment, actor, asked_by):
    """Заявка возвращается инициатору. `asked_by` — вид подзадачи, которая спросила:
    к ней заявка вернётся после ответа (хранится в `outcome` подзадачи инициатора)."""
    cursor.execute(
        "UPDATE payment_subtasks SET state = 'open', status = %%s, clarify_reason = %%s, clarify_comment = %%s, "
        "clarify_by = %%s, clarify_by_name = %%s, clarify_at = %s, outcome = %%s, opened_at = %s, "
        "done_at = NULL, done_by = NULL, done_by_name = NULL WHERE request_id = %%s AND kind = %%s"
        % (_NOW, _NOW),
        (status, reason, comment, (actor or {}).get('id'), (actor or {}).get('name'), asked_by,
         int(request_id), workflow.KIND_INITIATION))
    item = read_subtask(cursor, request_id, workflow.KIND_INITIATION)
    set_current(cursor, request_id, item)
    return item


def complete_subtask(cursor, subtask_id, actor, *, outcome=None, comment=None):
    cursor.execute(
        "UPDATE payment_subtasks SET state = 'done', done_at = %s, done_by = %%s, done_by_name = %%s, "
        "outcome = %%s, comment = %%s WHERE id = %%s" % _NOW,
        ((actor or {}).get('id'), (actor or {}).get('name'), outcome, comment, int(subtask_id)))


def set_subtask_assignee(cursor, request_id, kind, user, actor, *, reason=None):
    """Переназначить исполнителя подзадачи (None — вернуть её роли)."""
    cursor.execute(
        "UPDATE payment_subtasks SET assignee_id = %s, assignee_name = %s WHERE request_id = %s AND kind = %s "
        "RETURNING id, state, role_code",
        ((user or {}).get('id'), (user or {}).get('name'), int(request_id), kind))
    row = cursor.fetchone()
    if row and row[1] == 'open':
        cursor.execute(
            "UPDATE payment_requests SET current_assignee_id = %s, current_assignee_name = %s "
            "WHERE id = %s AND current_subtask_id = %s",
            ((user or {}).get('id'), (user or {}).get('name') or workflow.ROLE_LABELS.get(row[2]),
             int(request_id), row[0]))
    log_event(cursor, request_id, 'reassigned', actor, comment=reason,
              payload={'kind': kind, 'assignee_id': (user or {}).get('id'),
                       'assignee_name': (user or {}).get('name')})
    return row[0] if row else None


# ── История (п. 18) ──────────────────────────────────────────────────────────

def log_event(cursor, request_id, kind, actor, *, step_no=None, comment=None, payload=None):
    cursor.execute(
        "INSERT INTO payment_events (request_id, kind, step_no, actor_id, actor_name, comment, payload) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (int(request_id), kind, step_no, (actor or {}).get('id'), (actor or {}).get('name'),
         comment, Json(plain(payload)) if payload is not None else None))


_EVENT_FIELDS = ('id', 'kind', 'step_no', 'actor_id', 'actor_name', 'created_at', 'comment', 'payload')
# В общей ленте карточки этих событий нет: это журнал доступа к номеру карты, а не
# действие над заявкой. Показывается он администратору раздела.
HIDDEN_EVENT_KINDS = ('card_revealed',)


def list_events(cursor, request_id, *, with_hidden=False):
    """Лента заявки. `with_hidden` — вместе с обращениями к номеру карты: их
    видит администратор раздела (кто и когда смотрел номер)."""
    cursor.execute(
        "SELECT %s FROM payment_events WHERE request_id = %%s AND NOT (kind = ANY(%%s)) ORDER BY id"
        % ', '.join(_EVENT_FIELDS),
        (int(request_id), [] if with_hidden else list(HIDDEN_EVENT_KINDS)))
    return [_map(_EVENT_FIELDS, row) for row in cursor.fetchall()]


# ── Создание и правка ────────────────────────────────────────────────────────

def create_request(cursor, *, fields, items, actor, initiator, system_comment=None):
    """Заводит заявку с позициями и подзадачей инициатора. Возвращает id.

    Заявка рождается у инициатора (этап «Инициация»); на согласование её
    отправляет flow.submit — он же строит маршрут.
    """
    cols = ['initiator_id', 'initiator_name'] + list(EDITABLE_FIELDS)
    values = {'initiator_id': initiator.get('id'), 'initiator_name': initiator.get('name')}
    for col in EDITABLE_FIELDS:
        values[col] = fields.get(col)
    values['no_alternatives'] = bool(values.get('no_alternatives'))
    for extra in ('department_name', 'supplier_kind', 'supplier_vat'):
        if fields.get(extra) is not None:
            cols.append(extra)
            values[extra] = fields[extra]
    cols.append('stage')
    values['stage'] = workflow.STAGE_INITIATION
    cursor.execute(
        "INSERT INTO payment_requests (%s) VALUES (%s) RETURNING id"
        % (', '.join(cols), ', '.join(['%%(%s)s' % col for col in cols])),
        values)
    request_id = cursor.fetchone()[0]
    replace_items(cursor, request_id, items)
    ensure_initiation(cursor, request_id, initiator)
    log_event(cursor, request_id, 'created', actor, comment=system_comment)
    return request_id


def update_request_fields(cursor, request_id, fields):
    """Правит перечисленные поля заявки. Возвращает {поле: (было, стало)}."""
    if not fields:
        return {}
    cols = [col for col in fields if col in EDITABLE_FIELDS + PROCESS_FIELDS]
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


def mark_submitted(cursor, request_id, *, snapshot, basis=None, manager=None, set_manager=False,
                   reset_approval=False):
    """Заявка ушла от инициатора: снимок условий; при новом маршруте — ещё и
    руководитель с основанием согласования."""
    sets = ["submitted_at = %s" % _NOW, "submitted_snapshot = %s", "updated_at = %s" % _NOW]
    params = [Json(plain(snapshot))]
    if set_manager:
        sets += ["manager_id = %s", "manager_name = %s"]
        params += [(manager or {}).get('id'), (manager or {}).get('name')]
    if basis is not None:
        sets += ["route_basis = %s", "approver_user_id = %s", "approval_order_id = %s"]
        params += [Json(plain(basis)), basis.get('approver_user_id'), basis.get('limit_id')]
    if reset_approval:
        sets += ["approved_at = NULL", "approved_by = NULL", "approved_by_name = NULL", "amount_approved = NULL"]
    cursor.execute("UPDATE payment_requests SET %s WHERE id = %%s" % ', '.join(sets), params + [int(request_id)])


def touch_snapshot(cursor, request_id, snapshot):
    cursor.execute("UPDATE payment_requests SET submitted_snapshot = %s WHERE id = %s",
                   (Json(plain(snapshot)), int(request_id)))


def mark_snapshot_stale(cursor, request_id):
    """Номер карты сменили — снимок условий устарел: заявка согласуется заново."""
    cursor.execute(
        "UPDATE payment_requests SET submitted_snapshot = submitted_snapshot || '{\"stale\": true}'::jsonb "
        "WHERE id = %s AND submitted_snapshot IS NOT NULL", (int(request_id),))


def mark_approved(cursor, request_id, actor, amount):
    cursor.execute(
        "UPDATE payment_requests SET approved_at = %s, approved_by = %%s, approved_by_name = %%s, "
        "amount_approved = %%s, updated_at = %s WHERE id = %%s" % (_NOW, _NOW),
        ((actor or {}).get('id'), (actor or {}).get('name'), amount, int(request_id)))


def set_closing_docs(cursor, request_id, status):
    cursor.execute(
        "UPDATE payment_requests SET closing_docs_status = %%s, closing_docs_at = %s, updated_at = %s "
        "WHERE id = %%s" % (_NOW, _NOW),
        (status, int(request_id)))


def close_request(cursor, request_id, status, actor, comment=None):
    """Закрывает заявку: done — пройдена, rejected — отклонена, cancelled — отменена."""
    cursor.execute(
        "UPDATE payment_requests SET status = %%s, rejected_reason = %%s, closed_at = %s, updated_at = %s, "
        "stage = CASE WHEN %%s = 'done' THEN 'closed' ELSE stage END, "
        "current_subtask_id = CASE WHEN %%s = 'done' THEN NULL ELSE current_subtask_id END, "
        "current_role_code = NULL, current_assignee_id = NULL, current_assignee_name = NULL "
        "WHERE id = %%s" % (_NOW, _NOW),
        (status, comment if status != 'done' else None, status, status, int(request_id)))
    log_event(cursor, request_id, 'closed' if status == 'done' else status, actor, comment=comment)


def delete_request(cursor, request_id):
    cursor.execute("SELECT bucket, blob_path FROM payment_attachments WHERE request_id = %s", (int(request_id),))
    refs = [(row[0], row[1]) for row in cursor.fetchall()]
    cursor.execute("DELETE FROM payment_requests WHERE id = %s", (int(request_id),))
    return refs


# ── Вложения ─────────────────────────────────────────────────────────────────

_ATTACHMENT_FIELDS = ('id', 'request_id', 'step_no', 'subtask_kind', 'offer_id', 'kind', 'file_name',
                      'content_type', 'file_size', 'bucket', 'blob_path', 'uploaded_by',
                      'uploaded_by_name', 'uploaded_at')


def _attachment_row(row):
    item = _map(_ATTACHMENT_FIELDS, row)
    item['kind_label'] = workflow.ATTACHMENT_LABELS.get(item['kind'], item['kind'])
    item['stage_label'] = workflow.subtask_title(item['subtask_kind']) if item.get('subtask_kind') else None
    return item


def list_attachments(cursor, request_id):
    cursor.execute(
        "SELECT %s FROM payment_attachments WHERE request_id = %%s ORDER BY id"
        % ', '.join(_ATTACHMENT_FIELDS), (int(request_id),))
    return [_attachment_row(row) for row in cursor.fetchall()]


def public_attachment(item):
    """Без bucket/blob_path: фронту они не нужны, а в ответе служили бы подсказкой."""
    return {key: value for key, value in item.items() if key not in ('bucket', 'blob_path')}


def add_attachment(cursor, request_id, *, kind, file_name, content_type, file_size, bucket, blob_path,
                   actor, subtask_kind=None, offer_id=None):
    cursor.execute(
        """
        INSERT INTO payment_attachments (request_id, subtask_kind, offer_id, kind, file_name, content_type,
                                         file_size, bucket, blob_path, uploaded_by, uploaded_by_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (int(request_id), subtask_kind, offer_id, kind, file_name, content_type, int(file_size or 0),
         bucket, blob_path, (actor or {}).get('id'), (actor or {}).get('name')))
    attachment_id = cursor.fetchone()[0]
    log_event(cursor, request_id, 'attachment_added', actor,
              payload={'file_name': file_name, 'kind': kind, 'attachment_id': attachment_id,
                       'subtask_kind': subtask_kind})
    return attachment_id


def read_attachment(cursor, attachment_id):
    cursor.execute(
        "SELECT %s FROM payment_attachments WHERE id = %%s" % ', '.join(_ATTACHMENT_FIELDS),
        (int(attachment_id),))
    row = cursor.fetchone()
    return _attachment_row(row) if row else None


def remove_attachment(cursor, attachment_id, actor):
    item = read_attachment(cursor, attachment_id)
    if not item:
        return None
    cursor.execute("DELETE FROM payment_attachments WHERE id = %s", (int(attachment_id),))
    log_event(cursor, item['request_id'], 'attachment_removed', actor,
              payload={'file_name': item['file_name'], 'kind': item['kind']})
    return item


# ─────────────────────────────────────────────────────────────────────────────
# Дубль счёта (п. 8)
# ─────────────────────────────────────────────────────────────────────────────

def find_duplicates(cursor, request, *, visible_to=None, visible_roles=()):
    """Другие заявки с тем же поставщиком, номером счёта, суммой и датой счёта.

    ТЗ, п. 8: «система должна предупреждать о возможном дубле при совпадении
    поставщика, номера счёта, суммы и даты счёта» — совпасть должны все четыре.
    Отменённые и отклонённые заявки не считаются: по ним не платили.

    `visible_to` — граница видимости того, кому реестр целиком не положен: ему
    предупреждение называет только его же заявки, чужие он не видит и здесь.
    """
    number = str((request or {}).get('invoice_number') or '').strip()
    if not (request and request.get('counterparty_id') and number and request.get('invoice_date')):
        return []
    params = {'id': int(request['id']), 'cp': int(request['counterparty_id']), 'number': number,
              'date': request['invoice_date'], 'amount': workflow.to_decimal(request.get('amount'))}
    scope = ''
    if visible_to:
        params.update({'visible_to': int(visible_to), 'visible_roles': sorted(visible_roles or [])})
        scope = ' AND ' + _VISIBLE_SQL
    cursor.execute(
        """
        SELECT r.id, r.expense_name, r.status, r.stage, r.paid_on, r.paid_amount, r.amount, r.initiator_name,
               r.created_at
          FROM payment_requests r
         WHERE r.id <> %(id)s AND r.counterparty_id = %(cp)s AND lower(btrim(r.invoice_number)) = lower(%(number)s)
           AND r.invoice_date = %(date)s AND r.amount = %(amount)s AND r.status IN ('active', 'done')
        """ + scope + " ORDER BY r.id DESC LIMIT 10",
        params)
    return [{'id': row[0], 'expense_name': row[1], 'status': row[2], 'stage': row[3],
             'stage_label': workflow.STAGE_LABELS.get(row[3]), 'paid_on': row[4], 'paid_amount': row[5],
             'amount': row[6], 'initiator_name': row[7], 'created_at': row[8]} for row in cursor.fetchall()]


def duplicate_request_ids(cursor, request_ids):
    """Какие из заявок имеют возможный дубль — для отметки на доске бухгалтерии."""
    ids = sorted({int(x) for x in request_ids if x})
    if not ids:
        return set()
    cursor.execute(
        """
        SELECT DISTINCT a.id
          FROM payment_requests a
          JOIN payment_requests b
            ON b.id <> a.id AND b.counterparty_id = a.counterparty_id
           AND lower(btrim(b.invoice_number)) = lower(btrim(a.invoice_number))
           AND b.invoice_date = a.invoice_date AND b.amount = a.amount
           AND b.status IN ('active', 'done')
         WHERE a.id = ANY(%s) AND a.invoice_number IS NOT NULL AND btrim(a.invoice_number) <> ''
           AND a.invoice_date IS NOT NULL AND a.counterparty_id IS NOT NULL
        """,
        (ids,))
    return {row[0] for row in cursor.fetchall()}


# ─────────────────────────────────────────────────────────────────────────────
# Доски и рабочий стол (пп. 7–9, п. 16)
# ─────────────────────────────────────────────────────────────────────────────

# Сколько дней закрытая заявка остаётся в итоговых колонках доски. Доска — про
# текущую работу; историю смотрят в реестре.
BOARD_CLOSED_DAYS = 30
BOARD_MAX_REQUESTS = 600


def board_requests(cursor, board_code, *, own_user_id=None, own_roles=(), **filters):
    """Заявки доски вместе с их подзадачами: [(заявка, [подзадачи])].

    `own_user_id` — показывать только заявки, где у человека своя подзадача этой
    доски (доска согласования, п. 16). Без него — все заявки доски: задача
    подразделения видна каждому его участнику (п. 9).
    """
    board = workflow.board(board_code)
    params = {'kinds': list(board['kinds']), 'closed_days': BOARD_CLOSED_DAYS}
    where = _filter_clause(params, **filters_only(filters))
    scope = ''
    if own_user_id:
        params['own_user_id'] = int(own_user_id)
        params['own_roles'] = sorted(own_roles or [])
        # Своя заявка у роли целиком — не «моё согласование»: её утверждают другие.
        scope = (" AND (bs.assignee_id = %(own_user_id)s OR bs.done_by = %(own_user_id)s "
                 "OR (bs.assignee_id IS NULL AND bs.role_code = ANY(%(own_roles)s) "
                 "AND r.initiator_id IS DISTINCT FROM %(own_user_id)s))")
    exists = ("EXISTS (SELECT 1 FROM payment_subtasks bs WHERE bs.request_id = r.id "
              "AND bs.kind = ANY(%(kinds)s) AND bs.state IN ('open', 'waiting', 'done')" + scope + ")")
    recent = ("(r.status = 'active' OR r.closed_at IS NULL "
              "OR r.closed_at >= %s - make_interval(days => %%(closed_days)s))" % _NOW)
    where = (where + ' AND ' if where else ' WHERE ') + exists + ' AND ' + recent
    params['board_limit'] = BOARD_MAX_REQUESTS
    # Живые заявки — первыми: предел выборки не должен вытеснить с доски старую незакрытую.
    cursor.execute(_REQUEST_SQL + where + " ORDER BY (r.status = 'active') DESC, r.id DESC "
                   'LIMIT %(board_limit)s', params)
    requests = [_request_row(row) for row in cursor.fetchall()]
    by_request = subtasks_for_requests(cursor, [item['id'] for item in requests])
    return [(item, by_request.get(item['id'], [])) for item in requests]


# Открытая подзадача ждёт человека: назначена ему либо его роли. Согласование
# СВОЕЙ заявки у роли целиком его не ждёт — её утверждают другие (п. 16: «только
# те задачи, по которым от него требуется действие»); `approves_own` — исключение
# для администратора раздела, который на пилоте проходит маршрут за все роли.
_WAITS_FOR_ME_SQL = (
    "r.status = 'active' AND s.state = 'open' AND (s.assignee_id = %(user_id)s "
    "OR (s.assignee_id IS NULL AND s.role_code = ANY(%(roles)s) "
    "AND (%(approves_own)s OR s.kind <> ALL(%(approval_kinds)s) "
    "OR r.initiator_id IS DISTINCT FROM %(user_id)s)))")


def _desk_params(user_id, roles, approves_own):
    return {'user_id': int(user_id), 'roles': sorted(roles or []), 'approves_own': bool(approves_own),
            'approval_kinds': list(workflow.APPROVAL_KINDS), 'closing': workflow.KIND_CLOSING}


def desk_pairs(cursor, user_id, roles, approves_own=False):
    """Открытые подзадачи, которые ждут ЭТОГО человека, в порядке рабочего стола:
    [(id заявки, вид подзадачи)]. Сами заявки читает `desk_page` — только для
    страницы, которую показывают.

    П. 16: «каждый пользователь должен видеть только те задачи, по которым от
    него требуется действие» — лично назначенные и задачи его подразделения.
    Последние ключи сортировки — ради постраничного показа: при равных сроке и
    времени строки не должны меняться местами между запросами страниц.
    """
    cursor.execute(
        """
        SELECT s.request_id, s.kind
          FROM payment_subtasks s
          JOIN payment_requests r ON r.id = s.request_id
         WHERE """ + _WAITS_FOR_ME_SQL + """
         ORDER BY r.due_on NULLS LAST, s.opened_at, s.request_id, s.kind
        """,
        _desk_params(user_id, roles, approves_own))
    return [(row[0], row[1]) for row in cursor.fetchall()]


def desk_page(cursor, pairs):
    """(заявка, подзадача) для пар страницы — в том же порядке."""
    requests = {item['id']: item for item in requests_by_ids(cursor, sorted({pair[0] for pair in pairs}))}
    by_request = subtasks_for_requests(cursor, list(requests))
    rows = []
    for request_id, kind in pairs:
        subtask = next((s for s in by_request.get(request_id, []) if s['kind'] == kind), None)
        if request_id in requests and subtask:
            rows.append((requests[request_id], subtask))
    return rows


def desk_rows(cursor, user_id, roles, approves_own=False):
    """Все задачи стола разом: (заявка, подзадача)."""
    return desk_page(cursor, desk_pairs(cursor, user_id, roles, approves_own))


def desk_count(cursor, user_id, roles, approves_own=False):
    """Сколько задач ждут человека — число для вкладки «Мои задачи»."""
    cursor.execute(
        """
        SELECT (SELECT COUNT(*) FROM payment_subtasks s JOIN payment_requests r ON r.id = s.request_id
                 WHERE """ + _WAITS_FOR_ME_SQL + """)
             + (SELECT COUNT(*) FROM payment_requests r
                  JOIN payment_subtasks s ON s.request_id = r.id AND s.kind = %(closing)s AND s.state = 'open'
                 WHERE r.status = 'active' AND r.initiator_id = %(user_id)s AND r.closing_docs_status = 'none')
        """,
        _desk_params(user_id, roles, approves_own))
    return int(cursor.fetchone()[0] or 0)


def docs_wanted_ids(cursor, user_id):
    """Заявки инициатора, по которым бухгалтерия ждёт закрывающие документы,
    а он их ещё не приложил (п. 17: «необходимость приложить закрывающие документы»)."""
    cursor.execute(
        """
        SELECT r.id FROM payment_requests r
          JOIN payment_subtasks s ON s.request_id = r.id AND s.kind = %s AND s.state = 'open'
         WHERE r.status = 'active' AND r.initiator_id = %s AND r.closing_docs_status = 'none'
         ORDER BY r.id
        """,
        (workflow.KIND_CLOSING, int(user_id)))
    return [row[0] for row in cursor.fetchall()]


def docs_wanted(cursor, user_id):
    return requests_by_ids(cursor, docs_wanted_ids(cursor, user_id))


def participates(cursor, request_id, user_id, roles):
    """Есть ли у человека своя подзадача в заявке — лично или ролью."""
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM payment_subtasks s
             WHERE s.request_id = %(request_id)s AND s.state NOT IN ('pending', 'skipped')
               AND (s.assignee_id = %(user_id)s OR s.done_by = %(user_id)s
                    OR (s.assignee_id IS NULL AND s.role_code = ANY(%(roles)s))))
        """,
        {'request_id': int(request_id), 'user_id': int(user_id), 'roles': sorted(roles or [])})
    return bool(cursor.fetchone()[0])


# ─────────────────────────────────────────────────────────────────────────────
# Сроки (п. 17: «приближении срока», «просрочке»)
# ─────────────────────────────────────────────────────────────────────────────

def due_requests(cursor, today, soon_days):
    """Неоплаченные живые заявки со сроком: (заявка, 'soon' | 'overdue')."""
    cursor.execute(
        "SELECT id, due_on FROM payment_requests WHERE status = 'active' AND paid_on IS NULL "
        "AND due_on IS NOT NULL AND due_on <= %s::date + make_interval(days => %s)",
        (today, int(soon_days)))
    marks = {row[0]: ('overdue' if row[1] < today else 'soon') for row in cursor.fetchall()}
    return [(item, marks[item['id']]) for item in requests_by_ids(cursor, list(marks))]


# ─────────────────────────────────────────────────────────────────────────────
# Справка «История оплат»
# ─────────────────────────────────────────────────────────────────────────────

def payment_history(cursor, *, counterparty_id=None, category_id=None, subcategory_id=None,
                    expense_name=None, exclude_request_id=None, limit=5, visible_to=None, visible_roles=()):
    """Ранее ОПЛАЧЕННЫЕ заявки, похожие на заводимую: по контрагенту, по
    категории/подкатегории, по названию расхода. У каждой — дата последней
    оплаты, сумма и цена за единицу первой позиции (п. 9 дополнения Дмитриевой;
    ТЗ «Закуп и оплата», п. 8: бухгалтер проверяет «историю предыдущих оплат»).

    `visible_to` — граница видимости того, кому реестр целиком не положен: он
    видит прошлые оплаты только по своим заявкам, как и в реестре.
    """
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
        params['name_like'] = like_pattern(name)
        clauses.append("r.expense_name ILIKE %(name_like)s")
    if not clauses:
        return []
    where = ' AND (' + ' OR '.join(clauses) + ')'
    if exclude_request_id:
        params['exclude'] = int(exclude_request_id)
        where += ' AND r.id <> %(exclude)s'
    if visible_to:
        params.update({'visible_to': int(visible_to), 'visible_roles': sorted(visible_roles or [])})
        where += ' AND ' + _VISIBLE_SQL
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
