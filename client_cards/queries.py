# -*- coding: utf-8 -*-
"""SQL раздела «Карточка клиента».

Функции принимают ГОТОВЫЙ курсор (Database._get_cursor) и не управляют ни пулом, ни
транзакцией — их держит вызывающий: одно действие раздела меняет несколько таблиц
(карточка и её причины, запрос и его переписка), и один курсор держит их согласованными.

Время — наивное время Алматы; «сейчас» приходит аргументом (rules.now_almaty), а не
берётся в SQL: так его подставляют тесты и так оно одно на всё действие.
"""

import json

from psycopg2.extras import execute_values

from . import rules

# ─── Контекст доступа ────────────────────────────────────────────────────────

_ACCESS_CONTEXT_SQL = """
    SELECT u.name, u.role, u.status, u.department_id,
           ARRAY(SELECT h.id FROM departments h
                  WHERE h.head_user_id = u.id AND COALESCE(h.is_active, TRUE)),
           ARRAY(SELECT gom.group_id FROM group_operator_memberships gom
                  WHERE gom.operator_id = u.id AND gom.start_date <= CURRENT_DATE
                    AND (gom.end_date IS NULL OR gom.end_date >= CURRENT_DATE))
      FROM users u
     WHERE u.id = %(user_id)s
"""


def load_access_context(cursor, user_id):
    """Профиль для прав раздела. None — нет учётки.

    Группы — членство оператора на сегодня: по ним карточка включается не всему
    отделу, а линии (у Тез КЦ это группа техподдержки, без отдела продаж).
    """
    cursor.execute(_ACCESS_CONTEXT_SQL, {'user_id': int(user_id)})
    row = cursor.fetchone()
    if not row:
        return None
    name, role, status, department_id, headed, groups = row
    cursor.execute("SELECT department_id, enabled, group_ids FROM client_card_settings")
    departments = {int(dep): {'enabled': bool(enabled), 'group_ids': [int(x) for x in (group_ids or [])]}
                   for dep, enabled, group_ids in cursor.fetchall()}
    return {
        'user_id': int(user_id),
        'name': name or '',
        'role': str(role or '').strip().lower(),
        'status': status,
        'department_id': int(department_id) if department_id is not None else None,
        'headed_ids': [int(x) for x in (headed or [])],
        'group_ids': [int(x) for x in (groups or [])],
        'departments': departments,
    }


# ─── Настройки отдела ────────────────────────────────────────────────────────

_SETTINGS_COLUMNS = ('department_id', 'enabled', 'group_ids', 'wrapup_sec', 'alerts', 'alert_head',
                     'alert_chat_id', 'alert_chat_title', 'updated_by_name', 'updated_at')


def _settings(row):
    item = dict(zip(_SETTINGS_COLUMNS, row))
    alerts = item.get('alerts')
    if isinstance(alerts, str):
        alerts = json.loads(alerts or '{}')
    item['alerts'] = rules.normalize_alerts(alerts)
    item['group_ids'] = [int(x) for x in (item.get('group_ids') or [])]
    item['wrapup_sec'] = rules.normalize_wrapup(item.get('wrapup_sec'))
    return item


def get_settings(cursor, department_id):
    cursor.execute("SELECT " + ', '.join(_SETTINGS_COLUMNS) + " FROM client_card_settings WHERE department_id = %s",
                   (int(department_id),))
    row = cursor.fetchone()
    return _settings(row) if row else None


def provide_department(cursor, department_id):
    """Выдать раздел отделу: завести строку настроек (выключенной). Повтор ничего не меняет."""
    cursor.execute("""
        INSERT INTO client_card_settings (department_id) VALUES (%s)
        ON CONFLICT (department_id) DO NOTHING
    """, (int(department_id),))
    return get_settings(cursor, department_id)


def save_settings(cursor, department_id, fields, *, by, by_name, now):
    """Сохраняет названные поля настроек. Чего в fields нет — не трогает."""
    allowed = ('enabled', 'group_ids', 'wrapup_sec', 'alerts', 'alert_head', 'alert_chat_id', 'alert_chat_title')
    sets, params = [], []
    for key in allowed:
        if key not in fields:
            continue
        value = fields[key]
        if key == 'alerts':
            value = json.dumps(value, ensure_ascii=False)
        sets.append('%s = %%s' % key)
        params.append(value)
    if sets:
        params.extend([by, by_name, now, int(department_id)])
        cursor.execute("UPDATE client_card_settings SET " + ', '.join(sets)
                       + ", updated_by = %s, updated_by_name = %s, updated_at = %s WHERE department_id = %s",
                       params)
    return get_settings(cursor, department_id)


def departments(cursor, department_ids):
    """Отделы раздела с названиями — для переключателя у того, кто видит несколько."""
    ids = sorted({int(x) for x in department_ids or ()})
    if not ids:
        return []
    cursor.execute("""
        SELECT d.id, d.name, d.code, s.enabled
          FROM client_card_settings s
          JOIN departments d ON d.id = s.department_id
         WHERE d.id = ANY(%s)
         ORDER BY d.name, d.id
    """, (ids,))
    return [{'id': int(row[0]), 'name': row[1] or 'Отдел %s' % row[0], 'code': row[2], 'enabled': bool(row[3])}
            for row in cursor.fetchall()]


def all_departments(cursor):
    """Все действующие отделы — глобальному админу для «Выдать раздел отделу»."""
    cursor.execute("""
        SELECT d.id, d.name, (s.department_id IS NOT NULL)
          FROM departments d
          LEFT JOIN client_card_settings s ON s.department_id = d.id
         WHERE COALESCE(d.is_active, TRUE)
         ORDER BY d.name, d.id
    """)
    return [{'id': int(row[0]), 'name': row[1] or 'Отдел %s' % row[0], 'provided': bool(row[2])}
            for row in cursor.fetchall()]


def department_groups(cursor, department_id):
    """Действующие группы отдела — выбор «кому открывать карточку»."""
    cursor.execute("""
        SELECT id, name FROM groups
         WHERE department_id = %s AND status = 'active'
         ORDER BY name, id
    """, (int(department_id),))
    return [{'id': int(row[0]), 'name': row[1] or 'Группа %s' % row[0]} for row in cursor.fetchall()]


def user_department_id(cursor, user_id):
    cursor.execute("SELECT department_id FROM users WHERE id = %s", (int(user_id),))
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else None


# ─── Причины обращения ───────────────────────────────────────────────────────

def categories(cursor, department_id, *, include_inactive=False):
    """Справочник причин деревом: [{id, title, is_active, children: [...]}].

    used — в скольких обращениях причина стоит: скрытую с обращениями удалить
    нельзя, и редактор говорит это до нажатия, а не ошибкой после.
    """
    cursor.execute("""
        SELECT c.id, c.parent_id, c.title, c.is_active, c.position,
               (SELECT COUNT(*) FROM client_card_topics t
                 WHERE t.category_id = c.id OR t.subcategory_id = c.id)
          FROM client_card_categories c
         WHERE c.department_id = %s AND (%s OR c.is_active)
         ORDER BY c.position, c.id
    """, (int(department_id), bool(include_inactive)))
    roots, by_id, children = [], {}, []
    for cat_id, parent_id, title, is_active, position, used in cursor.fetchall():
        node = {'id': int(cat_id), 'title': title, 'is_active': bool(is_active),
                'position': int(position), 'used': int(used or 0)}
        if parent_id is None:
            node['children'] = []
            by_id[node['id']] = node
            roots.append(node)
        else:
            children.append((int(parent_id), node))
    for parent_id, node in children:
        parent = by_id.get(parent_id)
        # Подтема скрытой категории оператору не нужна; в редакторе категория есть всегда.
        if parent is not None:
            parent['children'].append(node)
    return roots


def category_index(cursor, department_id):
    """{id: {'parent_id', 'is_active', 'has_children', 'title'}} — для проверки выбора причин.

    has_children считает только видимые подтемы: категорию, у которой все подтемы
    скрыты, выбирают саму.
    """
    cursor.execute("""
        SELECT c.id, c.parent_id, c.is_active, c.title,
               EXISTS (SELECT 1 FROM client_card_categories k
                        WHERE k.parent_id = c.id AND k.is_active)
          FROM client_card_categories c
         WHERE c.department_id = %s
    """, (int(department_id),))
    return {int(row[0]): {'parent_id': int(row[1]) if row[1] is not None else None,
                          'is_active': bool(row[2]), 'title': row[3], 'has_children': bool(row[4])}
            for row in cursor.fetchall()}


def get_category(cursor, category_id):
    cursor.execute("""
        SELECT id, department_id, parent_id, title, is_active, position
          FROM client_card_categories WHERE id = %s
    """, (int(category_id),))
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': int(row[0]), 'department_id': int(row[1]),
            'parent_id': int(row[2]) if row[2] is not None else None,
            'title': row[3], 'is_active': bool(row[4]), 'position': int(row[5])}


def create_category(cursor, department_id, *, title, parent_id, by, now):
    """Новая причина — в конец своего уровня."""
    cursor.execute("""
        INSERT INTO client_card_categories (department_id, parent_id, title, position, created_by,
                                            created_at, updated_at)
        VALUES (%(dep)s, %(parent)s, %(title)s,
                (SELECT COALESCE(MAX(position), 0) + 1 FROM client_card_categories
                  WHERE department_id = %(dep)s AND parent_id IS NOT DISTINCT FROM %(parent)s),
                %(by)s, %(now)s, %(now)s)
        RETURNING id
    """, {'dep': int(department_id), 'parent': parent_id, 'title': title, 'by': by, 'now': now})
    return int(cursor.fetchone()[0])


def update_category(cursor, category_id, fields, *, now):
    sets, params = [], []
    for key in ('title', 'is_active'):
        if key in fields:
            sets.append('%s = %%s' % key)
            params.append(fields[key])
    if not sets:
        return
    params.extend([now, int(category_id)])
    cursor.execute("UPDATE client_card_categories SET " + ', '.join(sets) + ", updated_at = %s WHERE id = %s",
                   params)


def category_in_use(cursor, category_id):
    """Стоит ли причина (или её подтема) хоть в одном обращении."""
    cursor.execute("""
        SELECT EXISTS (
            SELECT 1 FROM client_card_topics t
             WHERE t.category_id = %(id)s OR t.subcategory_id = %(id)s
                OR t.subcategory_id IN (SELECT id FROM client_card_categories WHERE parent_id = %(id)s))
    """, {'id': int(category_id)})
    return bool(cursor.fetchone()[0])


def delete_category(cursor, category_id):
    cursor.execute("DELETE FROM client_card_categories WHERE id = %s", (int(category_id),))


def reorder_categories(cursor, department_id, parent_id, ordered_ids, *, now):
    """Порядок причин одного уровня — как в присланном списке. Чужие id молча не трогает."""
    ids = [int(x) for x in ordered_ids or ()]
    if not ids:
        return
    cursor.execute("""
        UPDATE client_card_categories c
           SET position = v.position, updated_at = %s
          FROM unnest(%s::int[]) WITH ORDINALITY AS v(id, position)
         WHERE c.id = v.id AND c.department_id = %s AND c.parent_id IS NOT DISTINCT FROM %s
    """, (now, ids, int(department_id), parent_id))


# ─── Аккаунты Тез и привязки номеров ─────────────────────────────────────────

_ACCOUNT_COLUMNS = ('id', 'kind', 'tez_id', 'full_name', 'phone', 'city', 'status',
                    'registered_at', 'last_order_at', 'details', 'fetched_at')
_ACCOUNT_SELECT = ', '.join('a.' + column for column in _ACCOUNT_COLUMNS)


def _account(row):
    item = dict(zip(_ACCOUNT_COLUMNS, row))
    details = item.get('details')
    if isinstance(details, str):
        details = json.loads(details or '{}')
    item['details'] = details or {}
    item['id'] = int(item['id'])
    item['tez_id'] = int(item['tez_id'])
    return item


def get_account(cursor, account_id):
    cursor.execute("SELECT " + _ACCOUNT_SELECT + " FROM client_card_accounts a WHERE a.id = %s",
                   (int(account_id),))
    row = cursor.fetchone()
    return _account(row) if row else None


def upsert_account(cursor, account, *, by, now, fetched):
    """Записать аккаунт и вернуть его id.

    fetched=True — профиль пришёл из TEZ APP: поля обновляются, fetched_at двигается.
    fetched=False — известны только вид и ID (ссылка оператора): уже записанный
    профиль не трогаем — пустые поля ссылки не должны стирать то, что отдавал Тез.
    """
    params = {
        'kind': account['kind'], 'tez_id': int(account['tez_id']),
        'full_name': account.get('full_name'), 'phone': account.get('phone'),
        'city': account.get('city'), 'status': account.get('status'),
        'registered_at': account.get('registered_at'), 'last_order_at': account.get('last_order_at'),
        'details': json.dumps(account.get('details') or {}, ensure_ascii=False, default=str),
        'fetched_at': now if fetched else None, 'by': by, 'now': now,
    }
    if fetched:
        cursor.execute("""
            INSERT INTO client_card_accounts (kind, tez_id, full_name, phone, city, status, registered_at,
                                              last_order_at, details, fetched_at, created_by, created_at)
            VALUES (%(kind)s, %(tez_id)s, %(full_name)s, %(phone)s, %(city)s, %(status)s, %(registered_at)s,
                    %(last_order_at)s, %(details)s, %(fetched_at)s, %(by)s, %(now)s)
            ON CONFLICT (kind, tez_id) DO UPDATE
               SET full_name = COALESCE(EXCLUDED.full_name, client_card_accounts.full_name),
                   phone = COALESCE(EXCLUDED.phone, client_card_accounts.phone),
                   city = COALESCE(EXCLUDED.city, client_card_accounts.city),
                   registered_at = COALESCE(EXCLUDED.registered_at, client_card_accounts.registered_at),
                   status = EXCLUDED.status, last_order_at = EXCLUDED.last_order_at,
                   details = EXCLUDED.details, fetched_at = EXCLUDED.fetched_at
            RETURNING id
        """, params)
    else:
        cursor.execute("""
            INSERT INTO client_card_accounts (kind, tez_id, created_by, created_at)
            VALUES (%(kind)s, %(tez_id)s, %(by)s, %(now)s)
            ON CONFLICT (kind, tez_id) DO UPDATE SET kind = EXCLUDED.kind
            RETURNING id
        """, params)
    return int(cursor.fetchone()[0])


def set_account_city(cursor, account_id, city):
    """Город аккаунта со слов оператора — пока его не отдал TEZ APP (service._set_city)."""
    cursor.execute("UPDATE client_card_accounts SET city = %s WHERE id = %s", (city, int(account_id)))


def link_phone(cursor, phone, account_id, *, origin, by, by_name, now):
    """Закрепить аккаунт за номером. Уже закреплён — ничего не меняем: кто и когда
    привязал первым, тот и автор."""
    cursor.execute("""
        INSERT INTO client_card_phone_links (phone, account_id, origin, created_by, created_by_name, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (phone, account_id) DO NOTHING
    """, (phone, int(account_id), origin, by, by_name, now))


def phone_accounts(cursor, phone):
    """Аккаунты, закреплённые за номером. Первым — тот, по которому с этого номера
    обращались последним: его оператор выберет скорее всего.

    «Обращались» — по заполненным карточкам: выбор аккаунта в идущем звонке порядок
    не меняет, иначе строки прыгали бы под рукой оператора."""
    cursor.execute("""
        SELECT """ + _ACCOUNT_SELECT + """,
               l.id, l.origin, l.created_by, l.created_by_name, l.created_at,
               (SELECT MAX(c.created_at) FROM client_cards c
                 WHERE c.account_id = a.id AND c.phone = l.phone AND c.status = 'done') AS last_used
          FROM client_card_phone_links l
          JOIN client_card_accounts a ON a.id = l.account_id
         WHERE l.phone = %s
         ORDER BY last_used DESC NULLS LAST, l.created_at DESC, l.id DESC
    """, (phone,))
    result = []
    width = len(_ACCOUNT_COLUMNS)
    for row in cursor.fetchall():
        account = _account(row[:width])
        account['link'] = {'id': int(row[width]), 'origin': row[width + 1], 'created_by': row[width + 2],
                           'created_by_name': row[width + 3], 'created_at': row[width + 4]}
        account['last_used_at'] = row[width + 5]
        result.append(account)
    return result


def get_link(cursor, link_id):
    cursor.execute("""
        SELECT id, phone, account_id, origin, created_by FROM client_card_phone_links WHERE id = %s
    """, (int(link_id),))
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': int(row[0]), 'phone': row[1], 'account_id': int(row[2]), 'origin': row[3], 'created_by': row[4]}


def unlink_phone(cursor, link_id):
    cursor.execute("DELETE FROM client_card_phone_links WHERE id = %s", (int(link_id),))


def account_phones(cursor, account_id):
    """Номера, с которых обращались по аккаунту, — «звонит и с этих номеров»."""
    cursor.execute("""
        SELECT phone FROM client_card_phone_links WHERE account_id = %s ORDER BY created_at, id
    """, (int(account_id),))
    return [row[0] for row in cursor.fetchall()]


# ─── Карточка звонка ─────────────────────────────────────────────────────────

_CARD_COLUMNS = ('id', 'department_id', 'operator_id', 'operator_name', 'phone', 'direction', 'call_key',
                 'status', 'account_id', 'account_kind', 'city', 'comment', 'parent_id', 'wrapup_sec',
                 'created_at', 'answered_at', 'ended_at', 'completed_at')
_CARD_SELECT = ', '.join('c.' + column for column in _CARD_COLUMNS)


def _card(row):
    item = dict(zip(_CARD_COLUMNS, row))
    item['id'] = int(item['id'])
    return item


def _attach_topics(cursor, cards):
    """Причины карточек — одним запросом на все: [{category_id, subcategory_id, title, sub_title}]."""
    ids = [card['id'] for card in cards]
    for card in cards:
        card['topics'] = []
    if not ids:
        return cards
    cursor.execute("""
        SELECT t.card_id, t.category_id, t.subcategory_id, c.title, s.title
          FROM client_card_topics t
          JOIN client_card_categories c ON c.id = t.category_id
          LEFT JOIN client_card_categories s ON s.id = t.subcategory_id
         WHERE t.card_id = ANY(%s)
         ORDER BY t.card_id, t.position, t.id
    """, (ids,))
    by_card = {card['id']: card for card in cards}
    for card_id, category_id, subcategory_id, title, sub_title in cursor.fetchall():
        by_card[int(card_id)]['topics'].append({
            'category_id': int(category_id),
            'subcategory_id': int(subcategory_id) if subcategory_id is not None else None,
            'title': title, 'sub_title': sub_title,
        })
    return cards


def open_card(cursor, *, department_id, operator_id, operator_name, phone, direction, call_key,
              wrapup_sec, now):
    """Карточка звонка: новая или уже заведённая по этому же звонку. Возвращает (id, создана)."""
    if call_key:
        cursor.execute("SELECT id FROM client_cards WHERE operator_id = %s AND call_key = %s",
                       (int(operator_id), call_key))
        row = cursor.fetchone()
        if row:
            return int(row[0]), False
    cursor.execute("""
        INSERT INTO client_cards (department_id, operator_id, operator_name, phone, direction, call_key,
                                  wrapup_sec, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (operator_id, call_key) WHERE call_key IS NOT NULL DO NOTHING
        RETURNING id
    """, (int(department_id), int(operator_id), operator_name, phone, direction, call_key or None,
          int(wrapup_sec), now, now))
    row = cursor.fetchone()
    if row:
        return int(row[0]), True
    # Гонка двух запросов одного звонка: строку завёл соседний.
    cursor.execute("SELECT id FROM client_cards WHERE operator_id = %s AND call_key = %s",
                   (int(operator_id), call_key))
    return int(cursor.fetchone()[0]), False


def get_card(cursor, card_id, *, for_update=False):
    cursor.execute("SELECT " + _CARD_SELECT + " FROM client_cards c WHERE c.id = %s"
                   + (" FOR UPDATE" if for_update else ""), (int(card_id),))
    row = cursor.fetchone()
    if not row:
        return None
    return _attach_topics(cursor, [_card(row)])[0]


def update_card(cursor, card_id, fields, *, now):
    """Меняет названные поля карточки."""
    allowed = ('status', 'account_id', 'account_kind', 'city', 'comment', 'parent_id', 'wrapup_sec',
               'answered_at', 'ended_at', 'completed_at')
    sets, params = [], []
    for key in allowed:
        if key in fields:
            sets.append('%s = %%s' % key)
            params.append(fields[key])
    params.extend([now, int(card_id)])
    cursor.execute("UPDATE client_cards SET " + ', '.join(sets + ['updated_at = %s']) + " WHERE id = %s", params)


def set_topics(cursor, card_id, topics):
    """Причины карточки — полной заменой, в присланном порядке."""
    cursor.execute("DELETE FROM client_card_topics WHERE card_id = %s", (int(card_id),))
    if topics:
        execute_values(cursor, """
            INSERT INTO client_card_topics (card_id, category_id, subcategory_id, position) VALUES %s
        """, [(int(card_id), category_id, subcategory_id, index)
              for index, (category_id, subcategory_id) in enumerate(topics)])


def operator_pending(cursor, operator_id, *, since):
    """Незаполненные карточки оператора со времени since: то, что ему осталось доделать."""
    cursor.execute("SELECT " + _CARD_SELECT + """
          FROM client_cards c
         WHERE c.operator_id = %s AND c.status = 'open' AND c.created_at >= %s
         ORDER BY c.created_at DESC, c.id DESC
         LIMIT 30
    """, (int(operator_id), since))
    return _attach_topics(cursor, [_card(row) for row in cursor.fetchall()])


def account_names(cursor, account_ids):
    """Подписи аккаунтов для списков: {id: {kind, tez_id, full_name, city}}."""
    ids = sorted({int(x) for x in account_ids if x})
    if not ids:
        return {}
    cursor.execute("SELECT id, kind, tez_id, full_name, city, phone FROM client_card_accounts WHERE id = ANY(%s)",
                   (ids,))
    return {int(row[0]): {'id': int(row[0]), 'kind': row[1], 'tez_id': int(row[2]), 'full_name': row[3],
                          'city': row[4], 'phone': row[5]} for row in cursor.fetchall()}


def _search_clause(search, params):
    """Условие поиска по обращениям: цифры — номер телефона или ID аккаунта, слова —
    комментарий, причина, имя аккаунта и оператора."""
    text = str(search or '').strip()
    if not text:
        return ''
    digits = ''.join(ch for ch in text if ch.isdigit())
    if digits and len(digits) >= 3 and not any(ch.isalpha() for ch in text):
        params['search_digits'] = '%' + digits + '%'
        params['search_id'] = int(digits) if len(digits) <= 12 else -1
        return """ AND (c.phone LIKE %(search_digits)s
                        OR EXISTS (SELECT 1 FROM client_card_accounts sa
                                    WHERE sa.id = c.account_id AND sa.tez_id = %(search_id)s))"""
    params['search_text'] = '%' + text.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_') + '%'
    return """ AND (c.comment ILIKE %(search_text)s
                    OR c.operator_name ILIKE %(search_text)s
                    OR EXISTS (SELECT 1 FROM client_card_accounts sa
                                WHERE sa.id = c.account_id AND sa.full_name ILIKE %(search_text)s)
                    OR EXISTS (SELECT 1 FROM client_card_topics st
                                 JOIN client_card_categories sc
                                   ON sc.id IN (st.category_id, st.subcategory_id)
                                WHERE st.card_id = c.id AND sc.title ILIKE %(search_text)s))"""


def history(cursor, department_id, *, account_id=None, phone=None, search=None, exclude_id=None,
            before_id=None, limit=30):
    """Обращения человека: по аккаунту и по номеру, с которого он звонит.

    Аккаунт выбран — его обращения с любых номеров плюс обращения с этого номера, где
    аккаунт так и не указали. Аккаунта нет — всё, что было с этого номера. Ни того, ни
    другого (поиск «по всем») — обращения отдела. Несостоявшиеся звонки не показываем.
    """
    params = {'dep': int(department_id), 'limit': int(limit) + 1}
    where = ["c.department_id = %(dep)s", "c.status <> 'void'"]
    if account_id and phone:
        params.update(account=int(account_id), phone=phone)
        where.append("(c.account_id = %(account)s OR (c.phone = %(phone)s AND c.account_id IS NULL))")
    elif account_id:
        params['account'] = int(account_id)
        where.append("c.account_id = %(account)s")
    elif phone:
        params['phone'] = phone
        where.append("c.phone = %(phone)s")
    if exclude_id:
        params['exclude'] = int(exclude_id)
        where.append("c.id <> %(exclude)s")
    if before_id:
        params['before'] = int(before_id)
        where.append("c.id < %(before)s")
    sql = ("SELECT " + _CARD_SELECT + " FROM client_cards c WHERE " + ' AND '.join(where)
           + _search_clause(search, params) + " ORDER BY c.id DESC LIMIT %(limit)s")
    cursor.execute(sql, params)
    rows = [_card(row) for row in cursor.fetchall()]
    has_more = len(rows) > int(limit)
    return _attach_topics(cursor, rows[:int(limit)]), has_more


def journal(cursor, department_id, *, operator_id=None, date_from=None, date_to=None, category_id=None,
            state=None, search=None, now=None, before_id=None, limit=50):
    """Журнал обращений отдела для портала. operator_id — «только мои» у оператора.

    state: done — заполненные, pending — не заполненные (звонок кончился или завис).
    """
    params = {'dep': int(department_id), 'limit': int(limit) + 1}
    where = ["c.department_id = %(dep)s", "c.status <> 'void'"]
    if operator_id:
        params['operator'] = int(operator_id)
        where.append("c.operator_id = %(operator)s")
    if date_from:
        params['date_from'] = date_from
        where.append("c.created_at >= %(date_from)s")
    if date_to:
        params['date_to'] = date_to
        where.append("c.created_at < %(date_to)s")
    if category_id:
        params['category'] = int(category_id)
        where.append("""EXISTS (SELECT 1 FROM client_card_topics jt
                                 WHERE jt.card_id = c.id
                                   AND %(category)s IN (jt.category_id, jt.subcategory_id))""")
    if state == 'done':
        where.append("c.status = 'done'")
    elif state == 'pending':
        where.append("c.status = 'open'")
    if before_id:
        params['before'] = int(before_id)
        where.append("c.id < %(before)s")
    sql = ("SELECT " + _CARD_SELECT + " FROM client_cards c WHERE " + ' AND '.join(where)
           + _search_clause(search, params) + " ORDER BY c.id DESC LIMIT %(limit)s")
    cursor.execute(sql, params)
    rows = [_card(row) for row in cursor.fetchall()]
    has_more = len(rows) > int(limit)
    return _attach_topics(cursor, rows[:int(limit)]), has_more


def card_requests(cursor, card_ids):
    """Запросы по карточкам: {card_id: [{id, destination_title, status, …}]}."""
    ids = sorted({int(x) for x in card_ids if x})
    if not ids:
        return {}
    cursor.execute("""
        SELECT r.card_id, r.id, r.destination_title, r.status, r.delivery_status, r.created_at,
               r.last_inbound_at
          FROM client_card_requests r
         WHERE r.card_id = ANY(%s)
         ORDER BY r.id
    """, (ids,))
    result = {}
    for card_id, request_id, title, status, delivery, created_at, inbound_at in cursor.fetchall():
        result.setdefault(int(card_id), []).append({
            'id': int(request_id), 'destination_title': title, 'status': status,
            'delivery_status': delivery, 'created_at': created_at, 'last_inbound_at': inbound_at})
    return result


# ─── Адресаты запросов ───────────────────────────────────────────────────────

_DESTINATION_COLUMNS = ('id', 'department_id', 'title', 'chat_id', 'chat_title', 'mention_usernames',
                        'position', 'is_active')


def _destination(row):
    item = dict(zip(_DESTINATION_COLUMNS, row))
    item['id'] = int(item['id'])
    item['is_ready'] = item['chat_id'] is not None
    return item


def destinations(cursor, department_id, *, include_inactive=False):
    cursor.execute("SELECT " + ', '.join(_DESTINATION_COLUMNS) + """
          FROM client_card_destinations
         WHERE department_id = %s AND (%s OR is_active)
         ORDER BY position, id
    """, (int(department_id), bool(include_inactive)))
    return [_destination(row) for row in cursor.fetchall()]


def get_destination(cursor, destination_id):
    cursor.execute("SELECT " + ', '.join(_DESTINATION_COLUMNS)
                   + " FROM client_card_destinations WHERE id = %s", (int(destination_id),))
    row = cursor.fetchone()
    return _destination(row) if row else None


def create_destination(cursor, department_id, fields, *, by, now):
    cursor.execute("""
        INSERT INTO client_card_destinations (department_id, title, chat_id, chat_title, mention_usernames,
                                              position, created_by, created_at, updated_at)
        VALUES (%(dep)s, %(title)s, %(chat_id)s, %(chat_title)s, %(mentions)s,
                (SELECT COALESCE(MAX(position), 0) + 1 FROM client_card_destinations
                  WHERE department_id = %(dep)s),
                %(by)s, %(now)s, %(now)s)
        RETURNING id
    """, {'dep': int(department_id), 'title': fields['title'], 'chat_id': fields.get('chat_id'),
          'chat_title': fields.get('chat_title'), 'mentions': fields.get('mention_usernames'),
          'by': by, 'now': now})
    return int(cursor.fetchone()[0])


def update_destination(cursor, destination_id, fields, *, now):
    sets, params = [], []
    for key in ('title', 'chat_id', 'chat_title', 'mention_usernames', 'is_active'):
        if key in fields:
            sets.append('%s = %%s' % key)
            params.append(fields[key])
    if not sets:
        return
    params.extend([now, int(destination_id)])
    cursor.execute("UPDATE client_card_destinations SET " + ', '.join(sets)
                   + ", updated_at = %s WHERE id = %s", params)


def destination_in_use(cursor, destination_id):
    cursor.execute("SELECT EXISTS (SELECT 1 FROM client_card_requests WHERE destination_id = %s)",
                   (int(destination_id),))
    return bool(cursor.fetchone()[0])


def delete_destination(cursor, destination_id):
    cursor.execute("DELETE FROM client_card_destinations WHERE id = %s", (int(destination_id),))


def move_chat(cursor, old_chat_id, new_chat_id, *, now):
    """Группа стала супергруппой: её id сменился навсегда — переносим адресатов и алерты."""
    cursor.execute("UPDATE client_card_destinations SET chat_id = %s, updated_at = %s WHERE chat_id = %s",
                   (int(new_chat_id), now, int(old_chat_id)))
    cursor.execute("UPDATE client_card_settings SET alert_chat_id = %s WHERE alert_chat_id = %s",
                   (int(new_chat_id), int(old_chat_id)))


def bot_chats(cursor):
    """Группы, где бот состоит, — для привязки адресата и чата алертов. Тот же реестр,
    что у «Обращений» и заявок в IT (it_ticket_channels)."""
    cursor.execute("""
        SELECT c.chat_id, c.title
          FROM it_ticket_channels c
         WHERE c.is_active AND c.chat_type IN ('group', 'supergroup')
         ORDER BY c.title NULLS LAST, c.chat_id
    """)
    return [{'chat_id': int(row[0]), 'title': row[1] or ('Чат %s' % row[0])} for row in cursor.fetchall()]


def chat_title(cursor, chat_id):
    """Название группы из реестра чатов бота. None — бот в ней не состоит."""
    cursor.execute("""
        SELECT title FROM it_ticket_channels
         WHERE chat_id = %s AND is_active AND chat_type IN ('group', 'supergroup')
    """, (int(chat_id),))
    row = cursor.fetchone()
    return (row[0] or 'Чат %s' % chat_id) if row else None


# ─── Запросы в другие отделы ─────────────────────────────────────────────────

_REQUEST_COLUMNS = ('id', 'department_id', 'card_id', 'destination_id', 'destination_title', 'body', 'status',
                    'account_id', 'phone', 'topic_title', 'created_by', 'created_by_name', 'tg_chat_id',
                    'tg_chat_title', 'tg_message_id', 'delivery_status', 'delivery_error', 'first_reply_at',
                    'last_message_at', 'last_inbound_at', 'author_unread_count', 'author_unread_at',
                    'resolved_at', 'resolved_by_name', 'created_at')
_REQUEST_SELECT = ', '.join('r.' + column for column in _REQUEST_COLUMNS)


def _request(row):
    item = dict(zip(_REQUEST_COLUMNS, row))
    item['id'] = int(item['id'])
    return item


def create_request(cursor, *, department_id, card_id, destination, body, account_id, phone, topic_title,
                   by, by_name, now):
    cursor.execute("""
        INSERT INTO client_card_requests (department_id, card_id, destination_id, destination_title, body,
                                          account_id, phone, topic_title, created_by, created_by_name,
                                          tg_chat_id, tg_chat_title, last_message_at, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (int(department_id), card_id, destination['id'], destination['title'], body, account_id, phone,
          topic_title, by, by_name, destination['chat_id'], destination['chat_title'], now, now, now))
    return int(cursor.fetchone()[0])


def get_request(cursor, request_id):
    cursor.execute("SELECT " + _REQUEST_SELECT + " FROM client_card_requests r WHERE r.id = %s",
                   (int(request_id),))
    row = cursor.fetchone()
    return _request(row) if row else None


def list_requests(cursor, department_ids, *, author_id=None, status=None, search=None, before=None, limit=40):
    """Запросы отделов; author_id — только свои (оператор). Порядок — по свежести переписки.

    before — (last_message_at, id) последней строки прошлой порции.
    """
    params = {'deps': [int(x) for x in department_ids], 'limit': int(limit) + 1}
    where = ["r.department_id = ANY(%(deps)s)"]
    if author_id:
        params['author'] = int(author_id)
        where.append("r.created_by = %(author)s")
    if status == 'active':
        where.append("r.status IN ('open', 'answered')")
    elif status in rules.REQUEST_STATUSES:
        params['status'] = status
        where.append("r.status = %(status)s")
    text = str(search or '').strip()
    if text:
        digits = ''.join(ch for ch in text if ch.isdigit())
        if digits and not any(ch.isalpha() for ch in text):
            # Цифры — номер запроса; от четырёх цифр ещё и кусок телефона или ID аккаунта.
            # Короче — только номер: «12» нашло бы каждый второй телефон.
            params['number'] = int(digits) if len(digits) <= 9 else -1
            if len(digits) >= 4:
                params['digits'] = '%' + digits + '%'
                params['tez_id'] = int(digits) if len(digits) <= 12 else -1
                where.append("""(r.id = %(number)s OR r.phone LIKE %(digits)s
                                 OR EXISTS (SELECT 1 FROM client_card_accounts sa
                                             WHERE sa.id = r.account_id AND sa.tez_id = %(tez_id)s))""")
            else:
                where.append("r.id = %(number)s")
        else:
            params['text'] = '%' + text.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_') + '%'
            where.append("""(r.body ILIKE %(text)s OR r.topic_title ILIKE %(text)s
                             OR r.destination_title ILIKE %(text)s OR r.created_by_name ILIKE %(text)s
                             OR EXISTS (SELECT 1 FROM client_card_accounts sa
                                         WHERE sa.id = r.account_id AND sa.full_name ILIKE %(text)s))""")
    if before:
        params['before_at'], params['before_id'] = before
        where.append("(r.last_message_at, r.id) < (%(before_at)s, %(before_id)s)")
    cursor.execute("SELECT " + _REQUEST_SELECT + " FROM client_card_requests r WHERE " + ' AND '.join(where)
                   + " ORDER BY r.last_message_at DESC, r.id DESC LIMIT %(limit)s", params)
    rows = [_request(row) for row in cursor.fetchall()]
    has_more = len(rows) > int(limit)
    return rows[:int(limit)], has_more


_MESSAGE_COLUMNS = ('id', 'request_id', 'direction', 'body', 'author_user_id', 'author_name', 'tg_chat_id',
                    'tg_message_id', 'tg_from_id', 'tg_username', 'reply_to_tg_message_id', 'file_ref',
                    'file_name', 'file_mime', 'file_size', 'file_kind', 'created_at')

# Нить длиннее не читают с экрана; переписка по одному запросу — десятки строк, не тысячи.
MESSAGES_LIMIT = 300


def _message(row):
    item = dict(zip(_MESSAGE_COLUMNS, row))
    item['id'] = int(item['id'])
    return item


def request_messages(cursor, request_id):
    cursor.execute("SELECT " + ', '.join(_MESSAGE_COLUMNS) + """
          FROM client_card_request_messages
         WHERE request_id = %s
         ORDER BY created_at, id
         LIMIT %s
    """, (int(request_id), MESSAGES_LIMIT))
    return [_message(row) for row in cursor.fetchall()]


def get_message(cursor, request_id, message_id):
    """Строка переписки в пределах ЭТОГО запроса: номеру сообщения с клиента верить нельзя."""
    cursor.execute("SELECT " + ', '.join(_MESSAGE_COLUMNS) + """
          FROM client_card_request_messages WHERE id = %s AND request_id = %s
    """, (int(message_id), int(request_id)))
    row = cursor.fetchone()
    return _message(row) if row else None


def add_message(cursor, *, request_id, direction, now, body=None, author_user_id=None, author_name=None,
                tg_chat_id=None, tg_message_id=None, tg_from_id=None, tg_username=None,
                reply_to_tg_message_id=None, file=None):
    """Строка переписки. None — такое сообщение Telegram уже записано (повтор апдейта)."""
    file = file or {}
    cursor.execute("""
        INSERT INTO client_card_request_messages (
            request_id, direction, body, author_user_id, author_name, tg_chat_id, tg_message_id,
            tg_from_id, tg_username, reply_to_tg_message_id, file_ref, file_name, file_mime, file_size,
            file_kind, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tg_chat_id, tg_message_id) WHERE tg_message_id IS NOT NULL DO NOTHING
        RETURNING id
    """, (int(request_id), direction, body, author_user_id, author_name, tg_chat_id, tg_message_id,
          tg_from_id, tg_username, reply_to_tg_message_id, file.get('ref'), file.get('name'),
          file.get('mime'), file.get('size'), file.get('kind'), now))
    row = cursor.fetchone()
    return int(row[0]) if row else None


def set_message_tg(cursor, message_id, *, chat_id, tg_message_id):
    """Сообщение ушло в группу: запомнить его номер там — по нему найдётся ответ."""
    cursor.execute("""
        UPDATE client_card_request_messages SET tg_chat_id = %s, tg_message_id = %s WHERE id = %s
    """, (chat_id, tg_message_id, int(message_id)))


def set_delivery(cursor, request_id, *, status, now, chat_id=None, chat_title=None, message_id=None,
                 error=None):
    cursor.execute("""
        UPDATE client_card_requests
           SET delivery_status = %s, delivery_error = %s,
               tg_chat_id = COALESCE(%s, tg_chat_id), tg_chat_title = COALESCE(%s, tg_chat_title),
               tg_message_id = COALESCE(%s, tg_message_id), updated_at = %s
         WHERE id = %s
    """, (status, error, chat_id, chat_title, message_id, now, int(request_id)))


def find_request_by_tg_message(cursor, chat_id, message_id):
    """Запрос, к переписке которого относится сообщение группы. None — не наше."""
    cursor.execute("""
        SELECT m.request_id FROM client_card_request_messages m
         WHERE m.tg_chat_id = %s AND m.tg_message_id = %s
         LIMIT 1
    """, (int(chat_id), int(message_id)))
    row = cursor.fetchone()
    return int(row[0]) if row else None


def touch_inbound(cursor, request_id, *, now):
    """Пришёл ответ из группы: «есть ответ», непрочитанное автору, свежесть переписки.

    Закрытый запрос ответом не переоткрываем: оператор решил, что вопрос снят, а
    группа дописала «ок» — это не новый вопрос. Непрочитанное при этом растёт.
    """
    cursor.execute("""
        UPDATE client_card_requests
           SET status = CASE WHEN status = 'open' THEN 'answered' ELSE status END,
               first_reply_at = COALESCE(first_reply_at, %(now)s),
               last_inbound_at = %(now)s, last_message_at = %(now)s,
               author_unread_count = author_unread_count + 1, author_unread_at = %(now)s,
               updated_at = %(now)s
         WHERE id = %(id)s
    """, {'now': now, 'id': int(request_id)})


def touch_outbound(cursor, request_id, *, now):
    cursor.execute("UPDATE client_card_requests SET last_message_at = %s, updated_at = %s WHERE id = %s",
                   (now, now, int(request_id)))


def set_request_status(cursor, request_id, status, *, by, by_name, now):
    """Сменить статус. Возвращает True, если он изменился."""
    closed = status in rules.REQUEST_CLOSED
    cursor.execute("""
        UPDATE client_card_requests
           SET status = %(status)s,
               resolved_at = CASE WHEN %(closed)s THEN %(now)s ELSE NULL END,
               resolved_by = CASE WHEN %(closed)s THEN %(by)s ELSE NULL END,
               resolved_by_name = CASE WHEN %(closed)s THEN %(by_name)s ELSE NULL END,
               updated_at = %(now)s
         WHERE id = %(id)s AND status <> %(status)s
    """, {'status': status, 'closed': closed, 'now': now, 'by': by, 'by_name': by_name, 'id': int(request_id)})
    return cursor.rowcount > 0


def mark_seen(cursor, request_id, user_id, *, now):
    """Автор открыл запрос — непрочитанное гаснет. Чужой просмотр его не трогает."""
    cursor.execute("""
        UPDATE client_card_requests
           SET author_unread_count = 0, author_unread_at = NULL, updated_at = %s
         WHERE id = %s AND created_by = %s AND author_unread_count > 0
    """, (now, int(request_id), int(user_id)))


def unread_total(cursor, user_id):
    """Сколько запросов автора ждут его: пришёл ответ, а он ещё не открывал."""
    cursor.execute("""
        SELECT COUNT(*) FROM client_card_requests WHERE created_by = %s AND author_unread_count > 0
    """, (int(user_id),))
    return int(cursor.fetchone()[0] or 0)


def unread_for_bell(cursor, user_id, limit):
    """Для колокола: (сколько всего, строки [id, подпись, когда])."""
    total = unread_total(cursor, user_id)
    if not total:
        return 0, []
    cursor.execute("""
        SELECT r.id, r.destination_title, r.topic_title, r.author_unread_at
          FROM client_card_requests r
         WHERE r.created_by = %s AND r.author_unread_count > 0
         ORDER BY r.author_unread_at DESC, r.id DESC
         LIMIT %s
    """, (int(user_id), int(limit)))
    return total, cursor.fetchall()


def user_names(cursor, ids):
    ids = sorted({int(x) for x in ids if x})
    if not ids:
        return {}
    cursor.execute("SELECT id, name FROM users WHERE id = ANY(%s)", (ids,))
    return {int(user_id): name or '' for user_id, name in cursor.fetchall()}


# ─── Алерты о росте обращений ────────────────────────────────────────────────

ALERT_LOCK_NAMESPACE = 'client_cards:alerts'

# «Один человек» в счёте обращений: аккаунт, а без него — номер. Пять звонков одного
# водителя — один человек, и волной это не считается.
_PERSON = "COALESCE('a' || c.account_id::text, 'p' || c.phone)"


def lock_alerts(cursor, department_id):
    """Одна проверка алертов на отдел за раз — до конца транзакции. Иначе две карточки,
    сохранённые в одну секунду, отправили бы руководителю одно и то же дважды."""
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s), %s)", (ALERT_LOCK_NAMESPACE, int(department_id)))


def alert_counts(cursor, department_id, *, since, until):
    """Сколько РАЗНЫХ людей обратилось по каждой причине за окно.

    Человек — аккаунт, а без него номер: пять звонков одного водителя — один человек.
    Возвращает {(category_id, subcategory_id | None): count} по подтемам и {category_id:
    count} по категориям целиком (разные люди на всю категорию, а не сумма подтем).
    Считаются заполненные карточки: у незаполненной причины ещё нет.
    """
    params = {'dep': int(department_id), 'since': since, 'until': until}
    # GROUPING(...) = 1 у строки-итога по категории: её не спутать со строкой «категория
    # выбрана без подтемы», где подтема тоже NULL.
    cursor.execute("""
        SELECT t.category_id, t.subcategory_id, GROUPING(t.subcategory_id),
               COUNT(DISTINCT """ + _PERSON + """)
          FROM client_cards c
          JOIN client_card_topics t ON t.card_id = c.id
         WHERE c.department_id = %(dep)s AND c.status = 'done'
           AND c.created_at >= %(since)s AND c.created_at <= %(until)s
         GROUP BY GROUPING SETS ((t.category_id, t.subcategory_id), (t.category_id))
    """, params)
    leaves, roots = {}, {}
    for category_id, subcategory_id, is_total, count in cursor.fetchall():
        if is_total:
            roots[int(category_id)] = int(count)
        elif subcategory_id is not None:
            leaves[(int(category_id), int(subcategory_id))] = int(count)
    return leaves, roots


def alert_baseline(cursor, department_id, *, until, window_min, days):
    """Сколько людей обычно обращается по причине в то же окно часов.

    Среднее по `days` прошлым дням: окно той же длины, кончающееся в тот же час и
    минуту. Дни без обращений считаются нулём — причина, по которой звонят раз в
    неделю, не должна выглядеть «обычно по пять». Тот же вид ответа, что у
    alert_counts: (по подтемам, по категориям).
    """
    params = {'dep': int(department_id), 'until': until, 'window': int(window_min), 'days': int(days)}
    cursor.execute("""
        WITH slots AS (
            SELECT %(until)s::timestamp - (g * interval '1 day') AS slot_until
              FROM generate_series(1, %(days)s) AS g
        ),
        per_slot AS (
            SELECT t.category_id, t.subcategory_id, GROUPING(t.subcategory_id) AS is_total,
                   COUNT(DISTINCT """ + _PERSON + """) AS people
              FROM slots s
              JOIN client_cards c
                ON c.department_id = %(dep)s AND c.status = 'done'
               AND c.created_at >= s.slot_until - (%(window)s * interval '1 minute')
               AND c.created_at < s.slot_until
              JOIN client_card_topics t ON t.card_id = c.id
             GROUP BY GROUPING SETS ((s.slot_until, t.category_id, t.subcategory_id),
                                     (s.slot_until, t.category_id))
        )
        SELECT category_id, subcategory_id, is_total, SUM(people)::float / %(days)s
          FROM per_slot
         GROUP BY category_id, subcategory_id, is_total
    """, params)
    leaves, roots = {}, {}
    for category_id, subcategory_id, is_total, average in cursor.fetchall():
        if is_total:
            roots[int(category_id)] = float(average or 0)
        elif subcategory_id is not None:
            leaves[(int(category_id), int(subcategory_id))] = float(average or 0)
    return leaves, roots


def last_alerts(cursor, department_id, *, day_start):
    """Прошлые алерты по причинам: {category_id: {'at', 'count', 'sent_today'}}."""
    cursor.execute("""
        SELECT DISTINCT ON (a.category_id) a.category_id, a.sent_at, a.count,
               (SELECT COUNT(*) FROM client_card_alerts d
                 WHERE d.department_id = a.department_id AND d.category_id = a.category_id
                   AND d.sent_at >= %(day_start)s)
          FROM client_card_alerts a
         WHERE a.department_id = %(dep)s
         ORDER BY a.category_id, a.sent_at DESC, a.id DESC
    """, {'dep': int(department_id), 'day_start': day_start})
    return {int(row[0]): {'at': row[1], 'count': int(row[2]), 'sent_today': int(row[3] or 0)}
            for row in cursor.fetchall()}


def alert_breakdown(cursor, department_id, node_id, *, since, until):
    """Города и подтемы волны — подробности для сообщения руководителю."""
    params = {'dep': int(department_id), 'node': int(node_id), 'since': since, 'until': until}
    cursor.execute("""
        SELECT NULLIF(c.city, ''), COUNT(DISTINCT """ + _PERSON + """) AS people
          FROM client_cards c
          JOIN client_card_topics t ON t.card_id = c.id
         WHERE c.department_id = %(dep)s AND c.status = 'done'
           AND c.created_at >= %(since)s AND c.created_at <= %(until)s
           AND %(node)s IN (t.category_id, t.subcategory_id)
         GROUP BY 1 ORDER BY people DESC, 1 LIMIT 5
    """, params)
    cities = [{'city': row[0], 'count': int(row[1])} for row in cursor.fetchall()]
    # Подтемы — только когда узел сам категория: у подтемы их нет.
    cursor.execute("""
        SELECT s.title, COUNT(DISTINCT """ + _PERSON + """) AS people
          FROM client_cards c
          JOIN client_card_topics t ON t.card_id = c.id
          JOIN client_card_categories s ON s.id = t.subcategory_id
         WHERE c.department_id = %(dep)s AND c.status = 'done'
           AND c.created_at >= %(since)s AND c.created_at <= %(until)s
           AND t.category_id = %(node)s
         GROUP BY 1 ORDER BY people DESC, 1 LIMIT 5
    """, params)
    subtopics = [{'title': row[0], 'count': int(row[1])} for row in cursor.fetchall()]
    return {'cities': cities, 'subtopics': subtopics}


def insert_alert(cursor, department_id, node, *, window_min, details, now):
    cursor.execute("""
        INSERT INTO client_card_alerts (department_id, category_id, kind, count, baseline, threshold,
                                        window_min, details, sent_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (int(department_id), int(node['id']), node['kind'], int(node['count']),
          round(float(node['baseline'] or 0), 2), int(node['threshold']), int(window_min),
          json.dumps(details, ensure_ascii=False), now))
    return int(cursor.fetchone()[0])


def mark_alerts_delivered(cursor, alert_ids):
    ids = [int(x) for x in alert_ids or ()]
    if ids:
        cursor.execute("UPDATE client_card_alerts SET delivered = TRUE WHERE id = ANY(%s)", (ids,))


def alert_recipients(cursor, department_id):
    """Кому уходит алерт: глава отдела лично (если включено и у него есть Telegram) и группа."""
    cursor.execute("""
        SELECT s.alert_head, s.alert_chat_id, u.telegram_id, u.name
          FROM client_card_settings s
          JOIN departments d ON d.id = s.department_id
          LEFT JOIN users u ON u.id = d.head_user_id
         WHERE s.department_id = %s
    """, (int(department_id),))
    row = cursor.fetchone()
    if not row:
        return []
    alert_head, chat_id, head_telegram, head_name = row
    targets = []
    if alert_head and head_telegram:
        targets.append({'chat_id': int(head_telegram), 'label': head_name or 'глава отдела', 'personal': True})
    if chat_id:
        targets.append({'chat_id': int(chat_id), 'label': 'группа', 'personal': False})
    return targets


def department_head(cursor, department_id):
    """Глава отдела для настройки алертов: имя и подключён ли у него Telegram."""
    cursor.execute("""
        SELECT u.name, (u.telegram_id IS NOT NULL)
          FROM departments d JOIN users u ON u.id = d.head_user_id
         WHERE d.id = %s
    """, (int(department_id),))
    row = cursor.fetchone()
    return {'name': row[0], 'has_telegram': bool(row[1])} if row else None


def alerts_log(cursor, department_id, limit=30):
    cursor.execute("""
        SELECT a.id, a.kind, a.count, a.baseline, a.threshold, a.window_min, a.sent_at, a.delivered,
               c.title, p.title
          FROM client_card_alerts a
          JOIN client_card_categories c ON c.id = a.category_id
          LEFT JOIN client_card_categories p ON p.id = c.parent_id
         WHERE a.department_id = %s
         ORDER BY a.sent_at DESC, a.id DESC
         LIMIT %s
    """, (int(department_id), int(limit)))
    return [{'id': int(row[0]), 'kind': row[1], 'count': int(row[2]), 'baseline': float(row[3] or 0),
             'threshold': int(row[4]), 'window_min': int(row[5]), 'sent_at': row[6], 'delivered': bool(row[7]),
             'title': row[8], 'parent_title': row[9]} for row in cursor.fetchall()]


# ─── Отчёт ───────────────────────────────────────────────────────────────────

# Состоявшиеся звонки отдела за период — общая часть всех разрезов отчёта.
_PERIOD_WHERE = """
     WHERE c.department_id = %(dep)s AND c.status <> 'void'
       AND c.created_at >= %(from)s AND c.created_at < %(to)s
"""
# Сколько секунд ушло на карточку после конца звонка, и сдана ли она позже срока
# постобработки. Карточку, сохранённую ещё во время разговора, в среднее не берём.
_WRAPUP_AVG = """AVG(EXTRACT(EPOCH FROM (c.completed_at - c.ended_at)))
                   FILTER (WHERE c.status = 'done' AND c.ended_at IS NOT NULL AND c.completed_at >= c.ended_at)"""
_LATE_COUNT = """COUNT(*) FILTER (WHERE c.status = 'done' AND c.ended_at IS NOT NULL
                                  AND c.completed_at > c.ended_at + (c.wrapup_sec * interval '1 second'))"""


def _seconds(value):
    return round(float(value), 1) if value is not None else None


def report(cursor, department_id, *, date_from, date_to):
    """Отчёт по обращениям за период [date_from, date_to) — причины, подтемы, города.

    Считаются звонки, которые состоялись (не void). В разрезах по причинам и городам —
    заполненные карточки: у незаполненной причины нет, она идёт отдельным числом.
    Карточка с двумя причинами входит в строки обеих, поэтому сумма строк бывает больше
    числа карточек; число по категории при этом — карточки с ЛЮБОЙ её подтемой, без
    двойного счёта.
    """
    params = {'dep': int(department_id), 'from': date_from, 'to': date_to}

    cursor.execute("""
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE c.status = 'done'),
               COUNT(*) FILTER (WHERE c.status = 'open'),
               COUNT(*) FILTER (WHERE c.direction = 'in'),
               COUNT(*) FILTER (WHERE c.direction = 'out'),
               COUNT(*) FILTER (WHERE c.status = 'done' AND c.account_kind = 'driver'),
               COUNT(*) FILTER (WHERE c.status = 'done' AND c.account_kind = 'client'),
               COUNT(DISTINCT """ + _PERSON + """),
               """ + _WRAPUP_AVG + """,
               """ + _LATE_COUNT + """
          FROM client_cards c
    """ + _PERIOD_WHERE, params)
    row = cursor.fetchone()
    totals = {
        'calls': int(row[0] or 0), 'done': int(row[1] or 0), 'pending': int(row[2] or 0),
        'incoming': int(row[3] or 0), 'outgoing': int(row[4] or 0),
        'drivers': int(row[5] or 0), 'clients': int(row[6] or 0), 'people': int(row[7] or 0),
        'avg_wrapup_sec': _seconds(row[8]), 'late': int(row[9] or 0),
    }

    cursor.execute("""
        SELECT k.id, k.title, s.id, s.title, COUNT(DISTINCT c.id)
          FROM client_cards c
          JOIN client_card_topics t ON t.card_id = c.id
          JOIN client_card_categories k ON k.id = t.category_id
          LEFT JOIN client_card_categories s ON s.id = t.subcategory_id
    """ + _PERIOD_WHERE + """ AND c.status = 'done'
         GROUP BY k.id, k.title, s.id, s.title
         ORDER BY 5 DESC, s.title NULLS LAST
    """, params)
    by_category, categories_list = {}, []
    for category_id, title, sub_id, sub_title, count in cursor.fetchall():
        node = by_category.get(category_id)
        if node is None:
            node = {'id': int(category_id), 'title': title, 'count': 0, 'subtopics': []}
            by_category[category_id] = node
            categories_list.append(node)
        node['subtopics'].append({'id': int(sub_id) if sub_id is not None else None,
                                  'title': sub_title, 'count': int(count)})
    cursor.execute("""
        SELECT t.category_id, COUNT(DISTINCT c.id)
          FROM client_cards c
          JOIN client_card_topics t ON t.card_id = c.id
    """ + _PERIOD_WHERE + " AND c.status = 'done' GROUP BY t.category_id", params)
    for category_id, count in cursor.fetchall():
        if category_id in by_category:
            by_category[category_id]['count'] = int(count)
    categories_list.sort(key=lambda node: (-node['count'], node['title'] or ''))

    cursor.execute("""
        SELECT COALESCE(c.city, ''), COUNT(*)
          FROM client_cards c
    """ + _PERIOD_WHERE + " AND c.status = 'done' GROUP BY 1 ORDER BY 2 DESC, 1", params)
    cities = [{'city': row[0], 'count': int(row[1])} for row in cursor.fetchall()]

    cursor.execute("""
        SELECT c.created_at::date, COUNT(*), COUNT(*) FILTER (WHERE c.status = 'done')
          FROM client_cards c
    """ + _PERIOD_WHERE + " GROUP BY 1 ORDER BY 1", params)
    days = [{'date': row[0], 'calls': int(row[1]), 'done': int(row[2])} for row in cursor.fetchall()]

    cursor.execute("""
        SELECT c.operator_id, MAX(c.operator_name), COUNT(*),
               COUNT(*) FILTER (WHERE c.status = 'done'),
               COUNT(*) FILTER (WHERE c.status = 'open'),
               """ + _WRAPUP_AVG + """,
               """ + _LATE_COUNT + """
          FROM client_cards c
    """ + _PERIOD_WHERE + " GROUP BY c.operator_id ORDER BY 3 DESC, 2", params)
    operators = [{'id': int(row[0]), 'name': row[1] or '', 'calls': int(row[2]), 'done': int(row[3]),
                  'pending': int(row[4]), 'avg_wrapup_sec': _seconds(row[5]), 'late': int(row[6] or 0)}
                 for row in cursor.fetchall()]

    cursor.execute("""
        SELECT COUNT(*), COUNT(*) FILTER (WHERE r.status IN ('open', 'answered')),
               AVG(EXTRACT(EPOCH FROM (r.first_reply_at - r.created_at)))
                   FILTER (WHERE r.first_reply_at IS NOT NULL)
          FROM client_card_requests r
         WHERE r.department_id = %(dep)s AND r.created_at >= %(from)s AND r.created_at < %(to)s
    """, params)
    row = cursor.fetchone()
    requests = {'total': int(row[0] or 0), 'active': int(row[1] or 0),
                'avg_first_reply_sec': _seconds(row[2])}

    return {'totals': totals, 'categories': categories_list, 'cities': cities, 'days': days,
            'operators': operators, 'requests': requests}
