"""SQL справочников раздела «Оплата счетов». Только запросы и разбор строк.

ТЗ «Закуп и оплата», п. 13 — восемь справочников: поставщики, договоры,
реквизиты наших юр. лиц, банковские реквизиты поставщиков, банковские карты,
регулярные платежи, лимиты и маршруты согласования. К ним — то, что было в
первой версии раздела (проекты, категории закупа) и чего требует учёт
имущества (категории имущества), и настройки процесса.

Номера карт здесь шифруются перед записью и наружу отдаются только маской;
полный номер читает отдельная функция `card_number` для тех, кому он положен.
"""

from psycopg2.extras import Json

from . import cards, workflow
from .sqlutil import NOW_SQL, columns, like_pattern, row_map

# ─── Проекты ─────────────────────────────────────────────────────────────────

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


# ─── Категории закупа ────────────────────────────────────────────────────────

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


# ─── Категории имущества ─────────────────────────────────────────────────────

def list_asset_categories(cursor, include_inactive=False):
    cursor.execute(
        "SELECT id, name, is_active FROM payment_asset_categories %s ORDER BY name"
        % ('' if include_inactive else 'WHERE is_active'))
    return [{'id': r[0], 'name': r[1], 'is_active': r[2]} for r in cursor.fetchall()]


def upsert_asset_category(cursor, *, category_id=None, name, is_active=True, actor_id=None):
    if category_id:
        cursor.execute("UPDATE payment_asset_categories SET name = %s, is_active = %s WHERE id = %s RETURNING id",
                       (name, bool(is_active), int(category_id)))
    else:
        cursor.execute(
            "INSERT INTO payment_asset_categories (name, is_active, created_by) VALUES (%s, %s, %s) RETURNING id",
            (name, bool(is_active), actor_id))
    row = cursor.fetchone()
    return row[0] if row else None


# ─── Наши юр. лица: реквизиты компаний (п. 13.3) ─────────────────────────────

_ENTITY_FIELDS = ('id', 'name', 'bin', 'kind', 'vat_payer', 'legal_address', 'bank_name', 'iik', 'bik',
                  'kbe', 'requisites', 'note', 'is_active')
_ENTITY_WRITE = ('name', 'bin', 'kind', 'vat_payer', 'legal_address', 'bank_name', 'iik', 'bik', 'kbe',
                 'requisites', 'note', 'is_active')


def requisites_text(entity):
    """Реквизиты компании одним текстом — то, что копируют и пересылают поставщику.
    Та же раскладка у кнопки «Скопировать реквизиты» во фронте (requisitesText)."""
    if not entity:
        return ''
    lines = [entity.get('name') or '']
    if entity.get('bin'):
        lines.append('БИН %s' % entity['bin'])
    if entity.get('legal_address'):
        lines.append('Юридический адрес: %s' % entity['legal_address'])
    if entity.get('iik'):
        lines.append('ИИК %s' % entity['iik'])
    if entity.get('bank_name'):
        lines.append('Банк: %s' % entity['bank_name'])
    if entity.get('bik'):
        lines.append('БИК %s' % entity['bik'])
    if entity.get('kbe'):
        lines.append('КБЕ %s' % entity['kbe'])
    lines.append('Плательщик НДС' if entity.get('vat_payer') else 'Без НДС')
    extra = str(entity.get('requisites') or '').strip()
    if extra:
        lines.append(extra)
    return '\n'.join(line for line in lines if line)


def list_legal_entities(cursor, include_inactive=False):
    cursor.execute(
        "SELECT %s FROM payment_legal_entities %s ORDER BY name"
        % (', '.join(_ENTITY_FIELDS), '' if include_inactive else 'WHERE is_active'))
    return [row_map(_ENTITY_FIELDS, row) for row in cursor.fetchall()]


def read_legal_entity(cursor, entity_id):
    if not entity_id:
        return None
    cursor.execute("SELECT %s FROM payment_legal_entities WHERE id = %%s" % ', '.join(_ENTITY_FIELDS),
                   (int(entity_id),))
    row = cursor.fetchone()
    return row_map(_ENTITY_FIELDS, row) if row else None


def upsert_legal_entity(cursor, *, entity_id=None, fields, actor_id=None):
    values = [fields.get(col) for col in _ENTITY_WRITE]
    if entity_id:
        cursor.execute(
            "UPDATE payment_legal_entities SET %s WHERE id = %%s RETURNING id"
            % ', '.join('%s = %%s' % col for col in _ENTITY_WRITE),
            values + [int(entity_id)])
    else:
        cursor.execute(
            "INSERT INTO payment_legal_entities (%s, created_by) VALUES (%s, %%s) RETURNING id"
            % (', '.join(_ENTITY_WRITE), ', '.join(['%s'] * len(_ENTITY_WRITE))),
            values + [actor_id])
    row = cursor.fetchone()
    return row[0] if row else None


# ─── Поставщики (п. 13.1) ────────────────────────────────────────────────────

_COUNTERPARTY_FIELDS = ('id', 'name', 'legal_name', 'bin', 'kind', 'vat_payer', 'requisites', 'contact',
                        'category_id', 'responsible_user_id', 'approver_user_id', 'approval_limit',
                        'note', 'is_active')
_COUNTERPARTY_WRITE = ('name', 'legal_name', 'bin', 'kind', 'vat_payer', 'requisites', 'contact',
                       'category_id', 'responsible_user_id', 'approver_user_id', 'approval_limit',
                       'note', 'is_active')
_COUNTERPARTY_SQL = """
    SELECT %s, cat.name, resp.name, appr.name,
           (SELECT COUNT(*) FROM payment_contracts c
             WHERE c.counterparty_id = cp.id AND c.status = 'active'
               AND (c.ends_on IS NULL OR c.ends_on >= (%s)::date))
      FROM payment_counterparties cp
      LEFT JOIN payment_categories cat ON cat.id = cp.category_id
      LEFT JOIN users resp ON resp.id = cp.responsible_user_id
      LEFT JOIN users appr ON appr.id = cp.approver_user_id
""" % (columns('cp', _COUNTERPARTY_FIELDS), NOW_SQL)


def _counterparty_row(row):
    item = row_map(_COUNTERPARTY_FIELDS, row[:len(_COUNTERPARTY_FIELDS)])
    extra = row[len(_COUNTERPARTY_FIELDS):]
    item['category_name'], item['responsible_name'], item['approver_name'] = extra[0], extra[1], extra[2]
    item['active_contracts'] = int(extra[3] or 0)
    return item


def list_counterparties(cursor, include_inactive=False, query=None):
    clauses, params = [], []
    if not include_inactive:
        clauses.append('cp.is_active')
    if query:
        clauses.append("(cp.name ILIKE %s OR COALESCE(cp.legal_name, '') ILIKE %s OR COALESCE(cp.bin, '') ILIKE %s)")
        like = like_pattern(query)
        params += [like, like, like]
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    cursor.execute(_COUNTERPARTY_SQL + where + ' ORDER BY cp.name', params)
    return [_counterparty_row(row) for row in cursor.fetchall()]


def read_counterparty(cursor, counterparty_id):
    if not counterparty_id:
        return None
    cursor.execute(_COUNTERPARTY_SQL + ' WHERE cp.id = %s', (int(counterparty_id),))
    row = cursor.fetchone()
    return _counterparty_row(row) if row else None


def upsert_counterparty(cursor, *, counterparty_id=None, fields, actor_id=None):
    values = [fields.get(col) for col in _COUNTERPARTY_WRITE]
    if counterparty_id:
        cursor.execute(
            "UPDATE payment_counterparties SET %s WHERE id = %%s RETURNING id"
            % ', '.join('%s = %%s' % col for col in _COUNTERPARTY_WRITE),
            values + [int(counterparty_id)])
    else:
        cursor.execute(
            "INSERT INTO payment_counterparties (%s, created_by) VALUES (%s, %%s) RETURNING id"
            % (', '.join(_COUNTERPARTY_WRITE), ', '.join(['%s'] * len(_COUNTERPARTY_WRITE))),
            values + [actor_id])
    row = cursor.fetchone()
    return row[0] if row else None


def find_counterparty_by_name(cursor, name):
    cursor.execute("SELECT id, name FROM payment_counterparties WHERE lower(name) = lower(%s)", (name,))
    row = cursor.fetchone()
    return {'id': row[0], 'name': row[1]} if row else None


def find_or_create_counterparty(cursor, name, actor_id=None):
    found = find_counterparty_by_name(cursor, name)
    if found:
        return found['id'], False
    created = upsert_counterparty(cursor, fields={'name': name, 'vat_payer': False, 'is_active': True},
                                  actor_id=actor_id)
    return created, True


# ─── Банковские реквизиты поставщиков (п. 13) ────────────────────────────────

_ACCOUNT_FIELDS = ('id', 'counterparty_id', 'bank_name', 'iik', 'bik', 'kbe', 'note', 'is_default', 'is_active')
_ACCOUNT_WRITE = ('counterparty_id', 'bank_name', 'iik', 'bik', 'kbe', 'note', 'is_default', 'is_active')


def account_text(account):
    """Счёт поставщика одной строкой: «KZ12…, АО „Банк“, БИК …, КБЕ 17»."""
    if not account:
        return ''
    parts = [account.get('iik') or '', account.get('bank_name') or '',
             'БИК %s' % account['bik'] if account.get('bik') else '',
             'КБЕ %s' % account['kbe'] if account.get('kbe') else '']
    return ', '.join(part for part in parts if part)


def list_accounts(cursor, counterparty_id=None, include_inactive=False):
    clauses, params = [], []
    if counterparty_id:
        clauses.append('a.counterparty_id = %s')
        params.append(int(counterparty_id))
    if not include_inactive:
        clauses.append('a.is_active')
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    cursor.execute(
        "SELECT %s, cp.name FROM payment_counterparty_accounts a "
        "JOIN payment_counterparties cp ON cp.id = a.counterparty_id%s "
        "ORDER BY cp.name, a.is_default DESC, a.id" % (columns('a', _ACCOUNT_FIELDS), where), params)
    result = []
    for row in cursor.fetchall():
        item = row_map(_ACCOUNT_FIELDS, row[:len(_ACCOUNT_FIELDS)])
        item['counterparty_name'] = row[len(_ACCOUNT_FIELDS)]
        item['text'] = account_text(item)
        result.append(item)
    return result


def read_account(cursor, account_id):
    if not account_id:
        return None
    cursor.execute("SELECT %s FROM payment_counterparty_accounts WHERE id = %%s" % ', '.join(_ACCOUNT_FIELDS),
                   (int(account_id),))
    row = cursor.fetchone()
    if not row:
        return None
    item = row_map(_ACCOUNT_FIELDS, row)
    item['text'] = account_text(item)
    return item


def upsert_account(cursor, *, account_id=None, fields, actor_id=None):
    values = [fields.get(col) for col in _ACCOUNT_WRITE]
    if fields.get('is_default') and fields.get('counterparty_id'):
        # Основной счёт у поставщика один: остальные перестают быть основными.
        cursor.execute(
            "UPDATE payment_counterparty_accounts SET is_default = FALSE WHERE counterparty_id = %s AND id <> %s",
            (int(fields['counterparty_id']), int(account_id or 0)))
    if account_id:
        cursor.execute(
            "UPDATE payment_counterparty_accounts SET %s WHERE id = %%s RETURNING id"
            % ', '.join('%s = %%s' % col for col in _ACCOUNT_WRITE),
            values + [int(account_id)])
    else:
        cursor.execute(
            "INSERT INTO payment_counterparty_accounts (%s, created_by) VALUES (%s, %%s) RETURNING id"
            % (', '.join(_ACCOUNT_WRITE), ', '.join(['%s'] * len(_ACCOUNT_WRITE))),
            values + [actor_id])
    row = cursor.fetchone()
    return row[0] if row else None


# ─── Договоры (п. 13.2) ──────────────────────────────────────────────────────

_CONTRACT_FIELDS = ('id', 'counterparty_id', 'legal_entity_id', 'number', 'signed_on', 'starts_on',
                    'ends_on', 'status', 'subject', 'amount', 'amount_limit', 'periodicity',
                    'responsible_user_id', 'file_name', 'file_type', 'file_size', 'note',
                    'created_at', 'updated_at')
_CONTRACT_WRITE = ('counterparty_id', 'legal_entity_id', 'number', 'signed_on', 'starts_on', 'ends_on',
                   'status', 'subject', 'amount', 'amount_limit', 'periodicity', 'responsible_user_id',
                   'note')
_CONTRACT_SQL = """
    SELECT %s, cp.name, le.name, resp.name
      FROM payment_contracts c
      LEFT JOIN payment_counterparties cp ON cp.id = c.counterparty_id
      LEFT JOIN payment_legal_entities le ON le.id = c.legal_entity_id
      LEFT JOIN users resp ON resp.id = c.responsible_user_id
""" % columns('c', _CONTRACT_FIELDS)


def _contract_row(row):
    item = row_map(_CONTRACT_FIELDS, row[:len(_CONTRACT_FIELDS)])
    extra = row[len(_CONTRACT_FIELDS):]
    item['counterparty_name'], item['legal_entity_name'], item['responsible_name'] = extra[0], extra[1], extra[2]
    item['has_file'] = bool(item.get('file_name'))
    return item


def list_contracts(cursor, counterparty_id=None):
    where, params = '', []
    if counterparty_id:
        where = ' WHERE c.counterparty_id = %s'
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
    values = [fields.get(col) for col in _CONTRACT_WRITE]
    if contract_id:
        cursor.execute(
            "UPDATE payment_contracts SET %s, updated_at = %s WHERE id = %%s RETURNING id"
            % (', '.join('%s = %%s' % col for col in _CONTRACT_WRITE), NOW_SQL),
            values + [int(contract_id)])
    else:
        cursor.execute(
            "INSERT INTO payment_contracts (%s, created_by) VALUES (%s, %%s) RETURNING id"
            % (', '.join(_CONTRACT_WRITE), ', '.join(['%s'] * len(_CONTRACT_WRITE))),
            values + [actor_id])
    row = cursor.fetchone()
    return row[0] if row else None


def contract_file(cursor, contract_id):
    """Где лежит файл договора: {'file_name', 'file_type', 'bucket', 'blob_path'} либо None."""
    cursor.execute(
        "SELECT file_name, file_type, file_bucket, file_blob FROM payment_contracts WHERE id = %s",
        (int(contract_id),))
    row = cursor.fetchone()
    if not row or not row[3]:
        return None
    return {'file_name': row[0], 'file_type': row[1], 'bucket': row[2], 'blob_path': row[3]}


def set_contract_file(cursor, contract_id, *, file_name=None, file_type=None, file_size=None, bucket=None,
                      blob_path=None):
    """Привязывает файл к договору (или снимает, если всё пусто). Возвращает прежний файл."""
    previous = contract_file(cursor, contract_id)
    cursor.execute(
        "UPDATE payment_contracts SET file_name = %%s, file_type = %%s, file_size = %%s, file_bucket = %%s, "
        "file_blob = %%s, updated_at = %s WHERE id = %%s" % NOW_SQL,
        (file_name, file_type, file_size, bucket, blob_path, int(contract_id)))
    return previous


# ─── Банковские карты (п. 13) ────────────────────────────────────────────────

_CARD_FIELDS = ('id', 'owner_kind', 'user_id', 'counterparty_id', 'holder_name', 'card_last4', 'note',
                'is_active')


def _card_row(row):
    item = row_map(_CARD_FIELDS, row[:len(_CARD_FIELDS)])
    item['owner_name'] = row[len(_CARD_FIELDS)] or row[len(_CARD_FIELDS) + 1]
    item['mask'] = cards.mask(item.get('card_last4'))
    return item


def list_cards(cursor, *, owner_kind=None, user_id=None, counterparty_id=None, include_inactive=False):
    """Карты справочника БЕЗ номеров: только маска и владелец."""
    clauses, params = [], []
    if owner_kind:
        clauses.append('k.owner_kind = %s')
        params.append(owner_kind)
    if user_id:
        clauses.append('k.user_id = %s')
        params.append(int(user_id))
    if counterparty_id:
        clauses.append('k.counterparty_id = %s')
        params.append(int(counterparty_id))
    if not include_inactive:
        clauses.append('k.is_active')
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    cursor.execute(
        "SELECT %s, u.name, cp.name FROM payment_cards k "
        "LEFT JOIN users u ON u.id = k.user_id "
        "LEFT JOIN payment_counterparties cp ON cp.id = k.counterparty_id%s "
        "ORDER BY k.owner_kind, k.holder_name, k.id" % (columns('k', _CARD_FIELDS), where), params)
    return [_card_row(row) for row in cursor.fetchall()]


def read_card(cursor, card_id):
    """Карта справочника вместе с зашифрованным номером (`card_number_enc`)."""
    if not card_id:
        return None
    cursor.execute(
        "SELECT %s, u.name, cp.name, k.card_number_enc FROM payment_cards k "
        "LEFT JOIN users u ON u.id = k.user_id "
        "LEFT JOIN payment_counterparties cp ON cp.id = k.counterparty_id WHERE k.id = %%s"
        % columns('k', _CARD_FIELDS), (int(card_id),))
    row = cursor.fetchone()
    if not row:
        return None
    item = _card_row(row[:-1])
    item['card_number_enc'] = row[-1]
    return item


def upsert_card(cursor, *, card_id=None, fields, number=None, actor_id=None):
    """Сохраняет карту. `number` — полный номер цифрами; при правке без номера
    прежний остаётся как был."""
    cols = ['owner_kind', 'user_id', 'counterparty_id', 'holder_name', 'note', 'is_active']
    values = [fields.get(col) for col in cols]
    if number:
        cols += ['card_number_enc', 'card_last4']
        values += [cards.encrypt(number), cards.last4(number)]
    if card_id:
        cursor.execute(
            "UPDATE payment_cards SET %s WHERE id = %%s RETURNING id"
            % ', '.join('%s = %%s' % col for col in cols), values + [int(card_id)])
    else:
        cursor.execute(
            "INSERT INTO payment_cards (%s, created_by) VALUES (%s, %%s) RETURNING id"
            % (', '.join(cols), ', '.join(['%s'] * len(cols))), values + [actor_id])
    row = cursor.fetchone()
    return row[0] if row else None


# ─── Лимиты согласования (п. 6, п. 13) — прежние «Приказы» ───────────────────

_LIMIT_FIELDS = ('id', 'number', 'issued_on', 'starts_on', 'ends_on', 'status', 'replaces_role',
                 'delegate_user_id', 'amount_limit', 'all_projects', 'all_counterparties',
                 'legal_entity_id', 'department_id', 'category_id', 'payment_method', 'request_kind',
                 'note', 'created_at', 'updated_at')
_LIMIT_WRITE = ('number', 'issued_on', 'starts_on', 'ends_on', 'status', 'replaces_role',
                'delegate_user_id', 'amount_limit', 'all_projects', 'all_counterparties',
                'legal_entity_id', 'department_id', 'category_id', 'payment_method', 'request_kind', 'note')
_LIMIT_SQL = """
    SELECT %s, u.name, le.name, d.name, cat.name,
           COALESCE((SELECT array_agg(op.project_id ORDER BY op.project_id)
                       FROM payment_order_projects op WHERE op.order_id = o.id), '{}'),
           COALESCE((SELECT array_agg(oc.counterparty_id ORDER BY oc.counterparty_id)
                       FROM payment_order_counterparties oc WHERE oc.order_id = o.id), '{}')
      FROM payment_approval_orders o
      LEFT JOIN users u ON u.id = o.delegate_user_id
      LEFT JOIN payment_legal_entities le ON le.id = o.legal_entity_id
      LEFT JOIN departments d ON d.id = o.department_id
      LEFT JOIN payment_categories cat ON cat.id = o.category_id
""" % columns('o', _LIMIT_FIELDS)


def _limit_row(row):
    size = len(_LIMIT_FIELDS)
    item = row_map(_LIMIT_FIELDS, row[:size])
    item['delegate_name'] = row[size]
    item['legal_entity_name'], item['department_name'], item['category_name'] = row[size + 1], row[size + 2], row[size + 3]
    item['project_ids'] = list(row[size + 4] or [])
    item['counterparty_ids'] = list(row[size + 5] or [])
    item['label'] = workflow.limit_label(item)
    return item


def list_limits(cursor, only_active=False):
    where = " WHERE o.status = 'active'" if only_active else ''
    cursor.execute(_LIMIT_SQL + where + " ORDER BY (o.status = 'active') DESC, o.id DESC")
    return [_limit_row(row) for row in cursor.fetchall()]


def upsert_limit(cursor, *, limit_id=None, fields, project_ids, counterparty_ids, actor_id=None):
    values = [fields.get(col) for col in _LIMIT_WRITE]
    if limit_id:
        cursor.execute(
            "UPDATE payment_approval_orders SET %s, updated_at = %s WHERE id = %%s RETURNING id"
            % (', '.join('%s = %%s' % col for col in _LIMIT_WRITE), NOW_SQL),
            values + [int(limit_id)])
    else:
        cursor.execute(
            "INSERT INTO payment_approval_orders (%s, created_by) VALUES (%s, %%s) RETURNING id"
            % (', '.join(_LIMIT_WRITE), ', '.join(['%s'] * len(_LIMIT_WRITE))),
            values + [actor_id])
    limit_id = cursor.fetchone()[0]
    cursor.execute("DELETE FROM payment_order_projects WHERE order_id = %s", (limit_id,))
    for project_id in sorted({int(x) for x in project_ids or []}):
        cursor.execute("INSERT INTO payment_order_projects (order_id, project_id) VALUES (%s, %s) "
                       "ON CONFLICT DO NOTHING", (limit_id, project_id))
    cursor.execute("DELETE FROM payment_order_counterparties WHERE order_id = %s", (limit_id,))
    for cp_id in sorted({int(x) for x in counterparty_ids or []}):
        cursor.execute("INSERT INTO payment_order_counterparties (order_id, counterparty_id) VALUES (%s, %s) "
                       "ON CONFLICT DO NOTHING", (limit_id, cp_id))
    return limit_id


# ─── Маршруты согласования (п. 6, п. 13) ─────────────────────────────────────

_ROUTE_FIELDS = ('id', 'name', 'position', 'legal_entity_id', 'department_id', 'category_id',
                 'payment_method', 'request_kind', 'amount_from', 'amount_to', 'manager_step',
                 'approver_user_id', 'is_active', 'note', 'created_at', 'updated_at')
_ROUTE_WRITE = ('name', 'position', 'legal_entity_id', 'department_id', 'category_id', 'payment_method',
                'request_kind', 'amount_from', 'amount_to', 'manager_step', 'approver_user_id',
                'is_active', 'note')
_ROUTE_SQL = """
    SELECT %s, u.name, le.name, d.name, cat.name
      FROM payment_approval_routes rt
      LEFT JOIN users u ON u.id = rt.approver_user_id
      LEFT JOIN payment_legal_entities le ON le.id = rt.legal_entity_id
      LEFT JOIN departments d ON d.id = rt.department_id
      LEFT JOIN payment_categories cat ON cat.id = rt.category_id
""" % columns('rt', _ROUTE_FIELDS)


def _route_row(row):
    size = len(_ROUTE_FIELDS)
    item = row_map(_ROUTE_FIELDS, row[:size])
    item['approver_name'], item['legal_entity_name'] = row[size], row[size + 1]
    item['department_name'], item['category_name'] = row[size + 2], row[size + 3]
    return item


def list_routes(cursor, only_active=False):
    where = ' WHERE rt.is_active' if only_active else ''
    cursor.execute(_ROUTE_SQL + where + ' ORDER BY rt.is_active DESC, rt.position, rt.id')
    return [_route_row(row) for row in cursor.fetchall()]


def upsert_route(cursor, *, route_id=None, fields, actor_id=None):
    values = [fields.get(col) for col in _ROUTE_WRITE]
    if route_id:
        cursor.execute(
            "UPDATE payment_approval_routes SET %s, updated_at = %s WHERE id = %%s RETURNING id"
            % (', '.join('%s = %%s' % col for col in _ROUTE_WRITE), NOW_SQL),
            values + [int(route_id)])
    else:
        cursor.execute(
            "INSERT INTO payment_approval_routes (%s, created_by) VALUES (%s, %%s) RETURNING id"
            % (', '.join(_ROUTE_WRITE), ', '.join(['%s'] * len(_ROUTE_WRITE))),
            values + [actor_id])
    row = cursor.fetchone()
    return row[0] if row else None


# ─── Настройки процесса ──────────────────────────────────────────────────────

def get_settings(cursor):
    """{ключ: значение} — сохранённое поверх значений по умолчанию."""
    cursor.execute("SELECT key, value FROM payment_settings")
    saved = {row[0]: row[1] for row in cursor.fetchall()}
    return {key: workflow.setting(saved, key) for key in workflow.SETTINGS_DEFAULTS}


def save_setting(cursor, key, value, actor_id=None):
    cursor.execute(
        "INSERT INTO payment_settings (key, value, updated_by) VALUES (%%s, %%s, %%s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, "
        "updated_at = %s" % NOW_SQL,
        (key, Json(value), actor_id))


# ─── Регулярные платежи (п. 4.4, п. 13) ──────────────────────────────────────

_TEMPLATE_FIELDS = ('id', 'name', 'amount', 'periodicity', 'interval_days', 'next_due_on', 'lead_days',
                    'project_id', 'branch', 'responsible_user_id', 'category_id', 'subcategory_id',
                    'counterparty_id', 'legal_entity_id', 'contract_id', 'counterparty_account_id',
                    'payment_purpose', 'amount_limit', 'approver_user_id', 'object_type',
                    'accounting_category', 'due_day', 'auto_create',
                    'note', 'is_active', 'last_generated_on', 'created_at', 'updated_at')
_TEMPLATE_WRITE = ('name', 'amount', 'periodicity', 'interval_days', 'next_due_on', 'lead_days', 'project_id',
                   'branch', 'responsible_user_id', 'category_id', 'subcategory_id', 'counterparty_id',
                   'legal_entity_id', 'contract_id', 'counterparty_account_id', 'payment_purpose',
                   'amount_limit', 'approver_user_id', 'object_type', 'accounting_category', 'due_day',
                   'auto_create', 'note', 'is_active')
_TEMPLATE_EXTRA = ('responsible_name', 'project_name', 'category_name', 'subcategory_name',
                   'counterparty_name', 'legal_entity_name', 'contract_number', 'approver_name',
                   'account_iik', 'account_bank', 'account_bik', 'account_kbe',
                   'requests_count', 'open_request_id')
_TEMPLATE_SQL = """
    SELECT %s, u.name, p.name, c1.name, c2.name, cp.name, le.name, ct.number, appr.name,
           acc.iik, acc.bank_name, acc.bik, acc.kbe,
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
      LEFT JOIN payment_contracts ct ON ct.id = t.contract_id
      LEFT JOIN users appr ON appr.id = t.approver_user_id
      LEFT JOIN payment_counterparty_accounts acc ON acc.id = t.counterparty_account_id
""" % columns('t', _TEMPLATE_FIELDS)


def _template_row(row):
    item = row_map(_TEMPLATE_FIELDS, row[:len(_TEMPLATE_FIELDS)])
    for key, value in zip(_TEMPLATE_EXTRA, row[len(_TEMPLATE_FIELDS):]):
        item[key] = value
    item['account_text'] = account_text({'iik': item.pop('account_iik'), 'bank_name': item.pop('account_bank'),
                                         'bik': item.pop('account_bik'), 'kbe': item.pop('account_kbe')})
    return item


def list_templates(cursor, include_inactive=True):
    where = '' if include_inactive else ' WHERE t.is_active'
    cursor.execute(_TEMPLATE_SQL + where + ' ORDER BY t.is_active DESC, t.next_due_on, t.name')
    return [_template_row(row) for row in cursor.fetchall()]


def read_template(cursor, template_id, *, lock=False):
    if not template_id:
        return None
    cursor.execute(_TEMPLATE_SQL + ' WHERE t.id = %s' + (' FOR UPDATE OF t' if lock else ''),
                   (int(template_id),))
    row = cursor.fetchone()
    return _template_row(row) if row else None


def upsert_template(cursor, *, template_id=None, fields, actor_id=None):
    values = [fields.get(col) for col in _TEMPLATE_WRITE]
    if template_id:
        cursor.execute(
            "UPDATE payment_fixed_templates SET %s, updated_at = %s WHERE id = %%s RETURNING id"
            % (', '.join('%s = %%s' % col for col in _TEMPLATE_WRITE), NOW_SQL),
            values + [int(template_id)])
    else:
        cursor.execute(
            "INSERT INTO payment_fixed_templates (%s, created_by) VALUES (%s, %%s) RETURNING id"
            % (', '.join(_TEMPLATE_WRITE), ', '.join(['%s'] * len(_TEMPLATE_WRITE))),
            values + [actor_id])
    row = cursor.fetchone()
    return row[0] if row else None


def advance_template(cursor, template_id, *, next_due_on, generated_on):
    cursor.execute(
        "UPDATE payment_fixed_templates SET next_due_on = %%s, last_generated_on = %%s, updated_at = %s "
        "WHERE id = %%s" % NOW_SQL,
        (next_due_on, generated_on, int(template_id)))


def template_requests(cursor, template_id):
    """Сколько заявок заведено по регулярному платежу."""
    cursor.execute("SELECT COUNT(*) FROM payment_requests WHERE fixed_template_id = %s", (int(template_id),))
    return int(cursor.fetchone()[0] or 0)


def delete_template(cursor, template_id):
    cursor.execute("DELETE FROM payment_fixed_templates WHERE id = %s", (int(template_id),))
    return cursor.rowcount


# ─── Общее ───────────────────────────────────────────────────────────────────

_NAMED_TABLES = {
    'projects': 'payment_projects', 'legal_entities': 'payment_legal_entities',
    'counterparties': 'payment_counterparties', 'categories': 'payment_categories',
    'asset_categories': 'payment_asset_categories',
}

# Справочник → таблица: что вообще можно удалять строкой. Белый список.
_DELETABLE = {
    'projects': 'payment_projects', 'categories': 'payment_categories',
    'legal_entities': 'payment_legal_entities', 'counterparties': 'payment_counterparties',
    'counterparty_accounts': 'payment_counterparty_accounts', 'contracts': 'payment_contracts',
    'cards': 'payment_cards', 'limits': 'payment_approval_orders', 'routes': 'payment_approval_routes',
    'asset_categories': 'payment_asset_categories',
}
DICTIONARIES = tuple(_DELETABLE)


def find_duplicate_name(cursor, name, *, dictionary, row_id=None, parent_id=None):
    """(id, название) другой записи справочника с тем же названием (без учёта регистра) или None.

    Названия уникальны индексом; проверка ДО записи нужна, чтобы ответить
    «уже есть» словами, а не упасть на нарушении индекса посреди транзакции.
    У категорий уникальность — внутри родителя.
    """
    table = _NAMED_TABLES.get(dictionary)
    if not table or not name:
        return None
    sql = "SELECT id, name FROM %s WHERE lower(name) = lower(%%s) AND id <> %%s" % table
    params = [name, int(row_id or 0)]
    if dictionary == 'categories':
        sql += " AND COALESCE(parent_id, 0) = %s"
        params.append(int(parent_id or 0))
    cursor.execute(sql + " LIMIT 1", params)
    row = cursor.fetchone()
    return (row[0], row[1]) if row else None


# Что держится за строку справочника: (таблица, колонка, где это видно человеку).
# Внешние ключи раздела стоят SET NULL и CASCADE — без этой проверки удаление
# поставщика молча обнулило бы его в оплаченных заявках и унесло бы договоры,
# реквизиты и карты, а удаление карты открыло бы её номер инициатору (у заявки
# пропал бы `card_id`, по которому номер считается «не своим»).
_USED_BY = {
    'counterparties': (
        ('payment_requests', 'counterparty_id', 'в заявках'),
        ('payment_request_offers', 'counterparty_id', 'в вариантах поставщиков'),
        ('payment_contracts', 'counterparty_id', 'в договорах'),
        ('payment_counterparty_accounts', 'counterparty_id', 'в банковских реквизитах'),
        ('payment_cards', 'counterparty_id', 'в картах'),
        ('payment_fixed_templates', 'counterparty_id', 'в регулярных платежах'),
        ('payment_order_counterparties', 'counterparty_id', 'в лимитах согласования'),
    ),
    'contracts': (
        ('payment_requests', 'contract_id', 'в заявках'),
        ('payment_fixed_templates', 'contract_id', 'в регулярных платежах'),
    ),
    'legal_entities': (
        ('payment_requests', 'legal_entity_id', 'в заявках'),
        ('payment_contracts', 'legal_entity_id', 'в договорах'),
        ('payment_fixed_templates', 'legal_entity_id', 'в регулярных платежах'),
        ('payment_approval_orders', 'legal_entity_id', 'в лимитах согласования'),
        ('payment_approval_routes', 'legal_entity_id', 'в маршрутах согласования'),
        ('payment_assets', 'legal_entity_id', 'в имуществе'),
    ),
    'counterparty_accounts': (
        ('payment_requests', 'counterparty_account_id', 'в заявках'),
        ('payment_fixed_templates', 'counterparty_account_id', 'в регулярных платежах'),
    ),
    'cards': (
        ('payment_requests', 'card_id', 'в заявках'),
    ),
    'categories': (
        ('payment_requests', 'category_id', 'в заявках'),
        ('payment_requests', 'subcategory_id', 'в заявках'),
        ('payment_categories', 'parent_id', 'в подкатегориях'),
        ('payment_fixed_templates', 'category_id', 'в регулярных платежах'),
        ('payment_fixed_templates', 'subcategory_id', 'в регулярных платежах'),
        ('payment_counterparties', 'category_id', 'в поставщиках'),
        ('payment_approval_orders', 'category_id', 'в лимитах согласования'),
        ('payment_approval_routes', 'category_id', 'в маршрутах согласования'),
    ),
    'asset_categories': (
        ('payment_assets', 'category_id', 'в имуществе'),
    ),
    'projects': (
        ('payment_requests', 'project_id', 'в заявках'),
        ('payment_fixed_templates', 'project_id', 'в регулярных платежах'),
        ('payment_order_projects', 'project_id', 'в лимитах согласования'),
    ),
    'limits': (
        ('payment_requests', 'approval_order_id', 'в заявках'),
    ),
}


def usage(cursor, dictionary, row_id):
    """Где строка справочника используется: [(где, сколько)] — пусто, если нигде."""
    found = {}
    for table, column, where in _USED_BY.get(dictionary, ()):
        cursor.execute("SELECT COUNT(*) FROM %s WHERE %s = %%s" % (table, column), (int(row_id),))
        count = int(cursor.fetchone()[0] or 0)
        if count:
            found[where] = found.get(where, 0) + count
    return list(found.items())


def delete_row(cursor, dictionary, row_id):
    """Удаление строки справочника. Таблица — только из белого списка."""
    cursor.execute("DELETE FROM %s WHERE id = %%s" % _DELETABLE[dictionary], (int(row_id),))
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
