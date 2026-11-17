"""Журнал раздела «Оплата счетов» (владелец 17.11.2026, п. 10: «логирование всех данных, прям всех»).

На вопрос с вариантами владелец выбрал: «Изменения и просмотры» — каждое изменение в разделе
(заявки, строки, файлы, справочники, договоры, регулярные платежи, роли, настройки): кто, когда,
было → стало; плюс кто открывал заявки и скачивал файлы. Смотрит журнал администратор раздела
отдельным экраном «Журнал», у записей справочников — «История».

Как устроено:

* Ход заявки по-прежнему пишет `payment_events` (лента карточки, п. 18 ТЗ). Журнал её не
  дублирует, а показывает вместе со своими строками (`journal`): одна лента на экране.
* Всё остальное — `payment_audit_log`: справочники, договоры, регулярные платежи, роли,
  настройки, письма поставщикам, имущество, а также просмотры заявок, скачивания файлов,
  выгрузки и автозаполнение из документов. Строку журнала нельзя ни поправить, ни стереть
  (триггер в schema.py).
* «Было → стало» у справочников считается по строке таблицы целиком (`snapshot` до и после):
  новое поле попадёт в журнал само, без правки этого модуля.
* Чего в журнале нет никогда: номера карты (хранится зашифрованным и в журнал не попадает
  даже им), ИИН (хвост ИИН с датой рождения и полом выдаёт весь номер — пишется только
  «изменён»), место файла в хранилище, служебные даты строки.
* Запись журнала не должна валить дело, ради которого пришёл человек: она идёт под своим
  SAVEPOINT, а сбой уходит в лог сервера.
"""

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from psycopg2.extras import Json

ACTION_LABELS = {
    'create': 'Создание', 'update': 'Изменение', 'delete': 'Удаление', 'view': 'Просмотр',
    'download': 'Скачивание', 'export': 'Выгрузка', 'extract': 'Автозаполнение', 'import': 'Загрузка файлом',
    'add': 'Добавление', 'remove': 'Снятие', 'run': 'Запуск',
}
ENTITY_LABELS = {
    'request': 'Заявка', 'attachment': 'Файл заявки', 'project': 'Проект', 'project_department': 'Отдел проекта',
    'category': 'Категория', 'asset_category': 'Категория имущества', 'legal_entity': 'Компания',
    'counterparty': 'Поставщик', 'counterparty_account': 'Реквизиты поставщика',
    'counterparty_email': 'Почта поставщика', 'contract': 'Договор', 'card': 'Карта', 'limit': 'Лимит согласования',
    'route': 'Маршрут согласования', 'template': 'Регулярный платёж', 'role': 'Роль', 'settings': 'Настройки',
    'mailing': 'Письмо поставщикам', 'asset': 'Имущество', 'registry': 'Реестр', 'document': 'Документ',
}
# Справочник API → сущность журнала (routes_directory: /dictionaries/<name>).
DICTIONARY_ENTITY = {
    'projects': 'project', 'project_departments': 'project_department', 'categories': 'category',
    'asset_categories': 'asset_category', 'legal_entities': 'legal_entity', 'counterparties': 'counterparty',
    'counterparty_accounts': 'counterparty_account', 'counterparty_emails': 'counterparty_email',
    'contracts': 'contract', 'cards': 'card', 'limits': 'limit', 'routes': 'route',
}
ENTITY_TABLES = {
    'project': 'payment_projects', 'project_department': 'payment_project_departments',
    'category': 'payment_categories', 'asset_category': 'payment_asset_categories',
    'legal_entity': 'payment_legal_entities', 'counterparty': 'payment_counterparties',
    'counterparty_account': 'payment_counterparty_accounts', 'counterparty_email': 'payment_counterparty_emails',
    'contract': 'payment_contracts', 'card': 'payment_cards', 'limit': 'payment_approval_orders',
    'route': 'payment_approval_routes', 'template': 'payment_fixed_templates', 'asset': 'payment_assets',
}

# Не пишутся вовсе: служебные даты строки, место файла в хранилище, зашифрованный номер карты.
_SKIP_KEYS = frozenset({
    'id', 'created_at', 'updated_at', 'created_by', 'file_bucket', 'file_blob', 'tax_file_bucket', 'tax_file_blob',
    'bucket', 'blob_path', 'card_number_enc', 'last_generated_on', 'tax_checked_at',
})
# Пишутся только словом «изменён»: полный номер карты и ИИН (см. заголовок модуля).
_SECRET_KEYS = frozenset({'card_number', 'iin', 'holder_iin'})
SECRET_MARK = 'изменён'

VIEW_REPEAT_MINUTES = 10      # повторное открытие той же заявки тем же человеком — не новая строка
PAGE_SIZE = 50
MAX_TEXT = 500


def plain(value):
    """Значение для JSON журнала: деньги — строкой без потери копеек, даты — ISO."""
    if isinstance(value, Decimal):
        # 10.00 и 10, 1.50 и 1.5 — одно и то же число: иначе журнал показал бы «изменение» без изменения.
        return format(value.normalize(), 'f')
    if isinstance(value, datetime):
        return value.isoformat(timespec='seconds')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[:MAX_TEXT - 1] + '…'
    return value


def _same(old, new):
    if old in (None, '', [], {}) and new in (None, '', [], {}):
        return True
    return plain(old) == plain(new)


def diff(before, after):
    """{поле: [было, стало]} — только изменившиеся поля, без служебных, секреты — словом."""
    before, after = before or {}, after or {}
    changes = {}
    for key in sorted(set(before) | set(after)):
        if key in _SKIP_KEYS:
            continue
        old, new = before.get(key), after.get(key)
        if _same(old, new):
            continue
        if key in _SECRET_KEYS:
            changes[key] = [None, SECRET_MARK]
        else:
            changes[key] = [plain(old), plain(new)]
    return changes


def created(after):
    """Поля новой записи — «было» пусто."""
    return diff({}, after)


def deleted(before):
    """Поля удалённой записи — «стало» пусто."""
    return diff(before, {})


def snapshot(cursor, entity, row_id):
    """Строка таблицы сущности целиком (to_jsonb) либо None."""
    table = ENTITY_TABLES.get(entity)
    if not table or not row_id:
        return None
    cursor.execute("SELECT to_jsonb(t) FROM %s t WHERE t.id = %%s" % table, (int(row_id),))
    row = cursor.fetchone()
    return dict(row[0]) if row and row[0] else None


def record(cursor, actor, action, entity, entity_id=None, *, request_id=None, title=None, changes=None, meta=None):
    """Пишет строку журнала. Изменение без изменений («сохранил, ничего не поменяв») не пишется."""
    if action == 'update' and changes is not None and not changes and not title:
        return None
    cursor.execute("SAVEPOINT payments_audit")
    try:
        cursor.execute(
            "INSERT INTO payment_audit_log (actor_id, actor_name, action, entity, entity_id, request_id, title, "
            "changes, meta) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            ((actor or {}).get('id'), (actor or {}).get('name'), action, entity,
             int(entity_id) if entity_id else None, int(request_id) if request_id else None,
             (str(title)[:MAX_TEXT] if title else None),
             Json(plain(changes)) if changes else None, Json(plain(meta)) if meta else None))
        row = cursor.fetchone()
        cursor.execute("RELEASE SAVEPOINT payments_audit")
        return row[0] if row else None
    except Exception:  # noqa: BLE001 — журнал не валит действие человека
        cursor.execute("ROLLBACK TO SAVEPOINT payments_audit")
        logging.exception('Оплата счетов: строка журнала не записана (%s %s %s)', action, entity, entity_id)
        return None


def record_change(cursor, actor, entity, entity_id, before, after, *, title=None, request_id=None, meta=None):
    """Создание, изменение или удаление записи по снимкам «до» и «после»."""
    if before is None and after is None:
        return None
    if before is None:
        action, changes = 'create', created(after)
    elif after is None:
        action, changes = 'delete', deleted(before)
    else:
        action, changes = 'update', diff(before, after)
        if not changes:
            return None
    return record(cursor, actor, action, entity, entity_id, request_id=request_id, title=title or _title(after or before),
                  changes=changes, meta=meta)


def _title(row):
    """Чем назвать запись в журнале: название, номер или адрес."""
    row = row or {}
    for key in ('name', 'number', 'email', 'holder_name', 'iik'):
        value = row.get(key)
        if value:
            return ('№%s' % value) if key == 'number' else str(value)
    return None


def record_view(cursor, actor, request_id, *, now=None):
    """Кто открывал заявку (п. 10). Повторное открытие той же заявки тем же человеком в течение
    VIEW_REPEAT_MINUTES — та же строка: окно заявки перечитывает её после каждого действия."""
    actor_id = (actor or {}).get('id')
    if not actor_id or not request_id:
        return None
    cursor.execute(
        "SELECT 1 FROM payment_audit_log WHERE entity = 'request' AND entity_id = %s AND action = 'view' "
        "AND actor_id = %s AND created_at > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') - %s LIMIT 1",
        (int(request_id), int(actor_id), timedelta(minutes=VIEW_REPEAT_MINUTES)))
    if cursor.fetchone():
        return None
    return record(cursor, actor, 'view', 'request', request_id, request_id=request_id)


# ─── Чтение ──────────────────────────────────────────────────────────────────

_AUDIT_COLUMNS = "a.id, a.created_at, a.actor_id, a.actor_name, a.action, a.entity, a.entity_id, a.request_id, " \
                 "a.title, a.changes, a.meta"


def _audit_row(row):
    return {
        'source': 'audit', 'id': row[0], 'created_at': row[1], 'actor_id': row[2], 'actor_name': row[3],
        'action': row[4], 'entity': row[5], 'entity_id': row[6], 'request_id': row[7], 'title': row[8],
        'changes': row[9] or None, 'meta': row[10] or None,
        'action_label': ACTION_LABELS.get(row[4], row[4]), 'entity_label': ENTITY_LABELS.get(row[5], row[5]),
    }


def history(cursor, entity, entity_id, *, limit=200):
    """«История» записи справочника: строки журнала о ней, новые сверху."""
    cursor.execute(
        "SELECT %s FROM payment_audit_log a WHERE a.entity = %%s AND a.entity_id = %%s "
        "ORDER BY a.id DESC LIMIT %%s" % _AUDIT_COLUMNS, (entity, int(entity_id), int(limit)))
    return [_audit_row(row) for row in cursor.fetchall()]


def journal(cursor, *, date_from=None, date_to=None, actor_id=None, entity=None, action=None, request_id=None,
            page=1, page_size=PAGE_SIZE, event_label=None):
    """Экран «Журнал»: строки журнала и ход заявок (payment_events) одной лентой, новые сверху.

    Фильтры: период (даты по Алматы, включительно), кто, что (сущность), действие, номер заявки.
    События заявок попадают в ленту, когда фильтр по сущности пуст или «Заявка», а по действию —
    пуст. Возвращает (строки, всего)."""
    page = max(1, int(page or 1))
    page_size = max(10, min(int(page_size or PAGE_SIZE), 200))
    audit_where, audit_params = ['TRUE'], []
    event_where, event_params = ['TRUE'], []
    if date_from:
        audit_where.append('a.created_at >= %s')
        event_where.append('e.created_at >= %s')
        audit_params.append(date_from)
        event_params.append(date_from)
    if date_to:
        audit_where.append('a.created_at < %s')
        event_where.append('e.created_at < %s')
        audit_params.append(date_to + timedelta(days=1))
        event_params.append(date_to + timedelta(days=1))
    if actor_id:
        audit_where.append('a.actor_id = %s')
        event_where.append('e.actor_id = %s')
        audit_params.append(int(actor_id))
        event_params.append(int(actor_id))
    if request_id:
        audit_where.append('(a.request_id = %s OR (a.entity = %s AND a.entity_id = %s))')
        audit_params += [int(request_id), 'request', int(request_id)]
        event_where.append('e.request_id = %s')
        event_params.append(int(request_id))
    if entity:
        audit_where.append('a.entity = %s')
        audit_params.append(entity)
    if action:
        audit_where.append('a.action = %s')
        audit_params.append(action)
    with_events = (not entity or entity == 'request') and not action
    parts = [
        "SELECT 'audit' AS source, a.id, a.created_at, a.actor_id, a.actor_name, a.action AS kind, a.entity, "
        "a.entity_id, a.request_id, a.title, a.changes AS payload, a.meta, NULL::text AS comment "
        "FROM payment_audit_log a WHERE " + ' AND '.join(audit_where)
    ]
    params = list(audit_params)
    if with_events:
        parts.append(
            "SELECT 'event' AS source, e.id, e.created_at, e.actor_id, e.actor_name, e.kind, 'request' AS entity, "
            "e.request_id AS entity_id, e.request_id, NULL::text AS title, e.payload, NULL::jsonb AS meta, e.comment "
            "FROM payment_events e WHERE " + ' AND '.join(event_where))
        params += event_params
    union = ' UNION ALL '.join('(%s)' % part for part in parts)
    cursor.execute("SELECT COUNT(*) FROM (%s) j" % union, params)
    total = int(cursor.fetchone()[0] or 0)
    cursor.execute(
        "SELECT * FROM (%s) j ORDER BY j.created_at DESC, j.id DESC LIMIT %%s OFFSET %%s" % union,
        params + [page_size, (page - 1) * page_size])
    rows = []
    for row in cursor.fetchall():
        source, kind = row[0], row[5]
        item = {
            'source': source, 'id': row[1], 'created_at': row[2], 'actor_id': row[3], 'actor_name': row[4],
            'action': kind, 'entity': row[6], 'entity_id': row[7], 'request_id': row[8], 'title': row[9],
            'entity_label': ENTITY_LABELS.get(row[6], row[6]), 'comment': row[12],
        }
        if source == 'audit':
            item.update(changes=row[10] or None, meta=row[11] or None, action_label=ACTION_LABELS.get(kind, kind))
        else:
            payload = row[10] or {}
            item.update(payload=payload, changes=(payload or {}).get('changes') or None,
                        action_label=(event_label(kind, payload) if event_label else kind))
        rows.append(item)
    return rows, total
