"""SQL раздела «Коды 2FA» поверх готового курсора. Ни Flask, ни Telegram.

Время во всех функциях — наивное время Алматы (rules.almaty), его передаёт сервис:
так тесты ведут часы сами, а не ждут настоящие 10 минут.

Ключ аккаунта (`secret_enc`) читает только get_account(..., with_secret=True) — для
расчёта кода. Ни один список и ни один ответ API его не содержит.
"""

from psycopg2 import errors as pg_errors

from . import access, crypto

# Сколько раз повторять «стереть цифры» / «убрать сообщение», если Telegram не ответил.
MAX_MESSAGE_ATTEMPTS = 5

_WORKING = "COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')"

# Глава маркетинга: глава ДЕЙСТВУЮЩЕГО отдела и сам работает. Уволили главу, а нового не
# назначили — подтверждать некому, и сервис скажет об этом прямо.
_APPROVER_SQL = """
    SELECT u.id, u.name, u.telegram_id
      FROM departments d
      JOIN users u ON u.id = d.head_user_id
     WHERE lower(d.code) = %(approver_code)s
       AND d.is_active
       AND {working}
     ORDER BY d.id
     LIMIT 1
""".format(working=_WORKING)

_ACCESS_CONTEXT_SQL = """
WITH me AS (
    SELECT id, name, role, status, telegram_id FROM users WHERE id = %(user_id)s
),
approver AS ({approver})
SELECT
    (SELECT name FROM me),
    (SELECT role FROM me),
    (SELECT status FROM me),
    (SELECT telegram_id FROM me),
    (SELECT id FROM approver),
    COALESCE((
        SELECT array_agg(g.account_id ORDER BY g.account_id)
          FROM twofa_grants g
          JOIN twofa_accounts a ON a.id = g.account_id AND a.is_active
         WHERE g.user_id = %(user_id)s
    ), '{{}}'),
    EXISTS (SELECT 1 FROM me)
""".format(approver=_APPROVER_SQL)


def load_access_context(cursor, user_id, key_status):
    """Профиль, глава маркетинга и выданные аккаунты одним запросом. None — нет учётки."""
    cursor.execute(_ACCESS_CONTEXT_SQL, {
        'user_id': int(user_id), 'approver_code': access.APPROVER_DEPARTMENT_CODE})
    row = cursor.fetchone()
    if not row or not row[6]:
        return None
    name, role, status, telegram_id, approver_id, granted, _exists = row
    return {
        'user_id': int(user_id),
        'name': name,
        'role': str(role or '').strip().lower(),
        'status': status,
        'telegram_id': int(telegram_id) if telegram_id else None,
        'approver_id': int(approver_id) if approver_id else None,
        'granted_account_ids': [int(x) for x in granted or ()],
        'key_status': key_status,
    }


def approver(cursor):
    cursor.execute(_APPROVER_SQL, {'approver_code': access.APPROVER_DEPARTMENT_CODE})
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': int(row[0]), 'name': row[1], 'telegram_id': int(row[2]) if row[2] else None}


def user_id_by_telegram(cursor, telegram_id):
    cursor.execute("SELECT id FROM users WHERE telegram_id = %s LIMIT 1", (int(telegram_id),))
    row = cursor.fetchone()
    return int(row[0]) if row else None


# ─── Аккаунты ──────────────────────────────────────────────────────────────────

_ACCOUNT_COLUMNS = "a.id, a.title, a.service, a.is_active, a.created_at"


def _account(row, with_secret=False):
    item = {'id': int(row[0]), 'title': row[1], 'service': row[2], 'is_active': bool(row[3]),
            'created_at': row[4]}
    if with_secret:
        item['secret_enc'] = row[5]
    return item


def get_account(cursor, account_id, with_secret=False, for_update=False):
    cursor.execute(
        "SELECT %s%s FROM twofa_accounts a WHERE a.id = %%s%s" % (
            _ACCOUNT_COLUMNS, ', a.secret_enc' if with_secret else '', ' FOR UPDATE' if for_update else ''),
        (int(account_id),))
    row = cursor.fetchone()
    return _account(row, with_secret) if row else None


def list_accounts(cursor, ids=None):
    """Живые аккаунты по названию; ids — только эти (то, что выдано сотруднику)."""
    params = []
    where = "a.is_active"
    if ids is not None:
        ids = [int(x) for x in ids]
        if not ids:
            return []
        where += " AND a.id = ANY(%s)"
        params.append(ids)
    cursor.execute(
        "SELECT %s FROM twofa_accounts a WHERE %s ORDER BY lower(a.title), a.id" % (_ACCOUNT_COLUMNS, where),
        params)
    return [_account(row) for row in cursor.fetchall()]


def next_account_id(cursor):
    """id будущего аккаунта — до вставки: шифротекст ключа привязан к id (crypto: AAD)."""
    cursor.execute("SELECT nextval(pg_get_serial_sequence('twofa_accounts', 'id'))")
    return int(cursor.fetchone()[0])


def insert_account(cursor, *, account_id, title, service, secret_enc, actor_id, now):
    cursor.execute("""
        INSERT INTO twofa_accounts (id, title, service, secret_enc, created_by, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (int(account_id), title, service, secret_enc, actor_id, now, now))
    return int(cursor.fetchone()[0])


def active_secrets(cursor):
    """[(id, title, secret_enc)] живых аккаунтов — для проверки «этот ключ уже добавлен»."""
    cursor.execute("SELECT id, title, secret_enc FROM twofa_accounts WHERE is_active ORDER BY id")
    return [(int(row[0]), row[1], row[2]) for row in cursor.fetchall()]


def rename_account(cursor, account_id, *, title, service, now):
    cursor.execute("""
        UPDATE twofa_accounts SET title = %s, service = %s, updated_at = %s
         WHERE id = %s AND is_active
    """, (title, service, now, int(account_id)))
    return cursor.rowcount == 1


def remove_account(cursor, account_id, *, actor_id, now):
    """Аккаунт уходит вместе с ключом: строка остаётся для журнала, ключа больше нет."""
    cursor.execute("""
        UPDATE twofa_accounts
           SET is_active = FALSE, secret_enc = NULL, removed_at = %s, removed_by = %s, updated_at = %s
         WHERE id = %s AND is_active
    """, (now, actor_id, now, int(account_id)))
    if cursor.rowcount != 1:
        return False
    cursor.execute("DELETE FROM twofa_grants WHERE account_id = %s", (int(account_id),))
    return True


# ─── Доступы ───────────────────────────────────────────────────────────────────

def grants_by_account(cursor):
    """{account_id: [user_id, …]} по живым аккаунтам и работающим людям."""
    cursor.execute("""
        SELECT g.account_id, g.user_id
          FROM twofa_grants g
          JOIN twofa_accounts a ON a.id = g.account_id AND a.is_active
          JOIN users u ON u.id = g.user_id
         WHERE {working}
         ORDER BY g.account_id, g.user_id
    """.format(working=_WORKING))
    result = {}
    for account_id, user_id in cursor.fetchall():
        result.setdefault(int(account_id), []).append(int(user_id))
    return result


_PERSON_COLUMNS = """
    u.id, u.name, u.telegram_id IS NOT NULL, COALESCE(u.status, 'working'),
    d.name, lower(COALESCE(d.code, ''))
"""


def _person(row):
    return {
        'id': int(row[0]), 'name': row[1], 'telegram_linked': bool(row[2]),
        'working': str(row[3] or 'working').lower() not in ('fired', 'dismissal'),
        'department': row[4], 'department_code': row[5],
    }


def people_with_access(cursor):
    """Все, кому выдан хоть один живой аккаунт, — с аккаунтами. Уволенные тоже: их доступ
    уже не действует, но строку видно, чтобы снять."""
    cursor.execute("""
        SELECT {columns}, array_agg(g.account_id ORDER BY g.account_id)
          FROM twofa_grants g
          JOIN twofa_accounts a ON a.id = g.account_id AND a.is_active
          JOIN users u ON u.id = g.user_id
          LEFT JOIN departments d ON d.id = u.department_id
         GROUP BY u.id, u.name, u.telegram_id, u.status, d.name, d.code
         ORDER BY lower(u.name), u.id
    """.format(columns=_PERSON_COLUMNS))
    people = []
    for row in cursor.fetchall():
        item = _person(row)
        item['account_ids'] = [int(x) for x in row[6] or ()]
        people.append(item)
    return people


def person(cursor, user_id):
    cursor.execute("""
        SELECT {columns}
          FROM users u
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE u.id = %s
    """.format(columns=_PERSON_COLUMNS), (int(user_id),))
    row = cursor.fetchone()
    return _person(row) if row else None


def search_people(cursor, query, limit=30):
    """Кому можно выдать доступ: работающие сотрудники. Маркетинг — первым: доступ почти
    всегда выдают своим, но запрета на другой отдел ТЗ не ставит."""
    text = ' '.join(str(query or '').split())
    params = {'code': access.APPROVER_DEPARTMENT_CODE, 'limit': int(limit)}
    name_clause = ''
    if text:
        params['pattern'] = '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        name_clause = 'AND (u.name ILIKE %(pattern)s OR u.login ILIKE %(pattern)s)'
    cursor.execute("""
        SELECT {columns}
          FROM users u
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE {working} {name_clause}
         ORDER BY (lower(COALESCE(d.code, '')) = %(code)s) DESC, lower(u.name), u.id
         LIMIT %(limit)s
    """.format(columns=_PERSON_COLUMNS, working=_WORKING, name_clause=name_clause), params)
    return [_person(row) for row in cursor.fetchall()]


def user_account_ids(cursor, user_id):
    cursor.execute("""
        SELECT g.account_id FROM twofa_grants g
          JOIN twofa_accounts a ON a.id = g.account_id AND a.is_active
         WHERE g.user_id = %s ORDER BY g.account_id
    """, (int(user_id),))
    return [int(row[0]) for row in cursor.fetchall()]


def set_user_grants(cursor, user_id, account_ids, *, actor_id, now):
    """Заменить набор аккаунтов человека. Возвращает (добавленные, снятые)."""
    wanted = {int(x) for x in account_ids}
    current = set(user_account_ids(cursor, user_id))
    added = sorted(wanted - current)
    removed = sorted(current - wanted)
    if removed:
        cursor.execute("DELETE FROM twofa_grants WHERE user_id = %s AND account_id = ANY(%s)",
                       (int(user_id), removed))
    for account_id in added:
        cursor.execute("""
            INSERT INTO twofa_grants (account_id, user_id, granted_by, granted_at)
            VALUES (%s, %s, %s, %s) ON CONFLICT (account_id, user_id) DO NOTHING
        """, (account_id, int(user_id), actor_id, now))
    return added, removed


# ─── Запросы ───────────────────────────────────────────────────────────────────

_REQUEST_COLUMNS = """
    r.id, r.account_id, a.title, a.service, r.requester_id, ru.name, ru.telegram_id,
    r.status, r.created_at, r.expires_at, r.decided_at, r.decided_by, du.name, r.decided_via,
    r.resend_until, r.codes_sent, r.delivery_error, r.last_counter, r.last_issued_at
"""

_REQUEST_FROM = """
      FROM twofa_requests r
      JOIN twofa_accounts a ON a.id = r.account_id
      LEFT JOIN users ru ON ru.id = r.requester_id
      LEFT JOIN users du ON du.id = r.decided_by
"""


def _request(row):
    return {
        'id': int(row[0]), 'account_id': int(row[1]), 'account_title': row[2], 'service': row[3],
        'requester_id': int(row[4]), 'requester_name': row[5],
        'requester_telegram_id': int(row[6]) if row[6] else None,
        'status': row[7], 'created_at': row[8], 'expires_at': row[9], 'decided_at': row[10],
        'decided_by': int(row[11]) if row[11] else None, 'decided_by_name': row[12],
        'decided_via': row[13], 'resend_until': row[14], 'codes_sent': int(row[15] or 0),
        'delivery_error': row[16], 'last_counter': int(row[17]) if row[17] is not None else None,
        'last_issued_at': row[18],
    }


def get_request(cursor, request_id, for_update=False):
    cursor.execute("SELECT %s %s WHERE r.id = %%s%s" % (
        _REQUEST_COLUMNS, _REQUEST_FROM, ' FOR UPDATE OF r' if for_update else ''), (int(request_id),))
    row = cursor.fetchone()
    return _request(row) if row else None


def pending_of(cursor, account_id, requester_id, now):
    """Живой ждущий запрос этой пары: тот, что ещё не сгорел."""
    cursor.execute("SELECT %s %s WHERE r.account_id = %%s AND r.requester_id = %%s "
                   "AND r.status = 'pending' AND r.expires_at > %%s" % (_REQUEST_COLUMNS, _REQUEST_FROM),
                   (int(account_id), int(requester_id), now))
    row = cursor.fetchone()
    return _request(row) if row else None


def insert_request(cursor, *, account_id, requester_id, now, expires_at):
    """Новый запрос. None — пара уже ждёт (гонка двух вкладок упёрлась в уникальный индекс)."""
    cursor.execute('SAVEPOINT twofa_insert_request')
    try:
        cursor.execute("""
            INSERT INTO twofa_requests (account_id, requester_id, status, created_at, expires_at)
            VALUES (%s, %s, 'pending', %s, %s)
            RETURNING id
        """, (int(account_id), int(requester_id), now, expires_at))
        request_id = int(cursor.fetchone()[0])
    except pg_errors.UniqueViolation:
        cursor.execute('ROLLBACK TO SAVEPOINT twofa_insert_request')
        cursor.execute('RELEASE SAVEPOINT twofa_insert_request')
        return None
    cursor.execute('RELEASE SAVEPOINT twofa_insert_request')
    return request_id


def decide(cursor, request_id, *, status, actor_id, via, now, resend_until=None):
    """Решение по запросу. False — его уже решили или он сгорел: первое нажатие выигрывает."""
    cursor.execute("""
        UPDATE twofa_requests
           SET status = %s, decided_at = %s, decided_by = %s, decided_via = %s, resend_until = %s
         WHERE id = %s AND status = 'pending' AND expires_at > %s
    """, (status, now, actor_id, via, resend_until, int(request_id), now))
    return cursor.rowcount == 1


def claim_expired(cursor, now):
    cursor.execute("""
        UPDATE twofa_requests SET status = 'expired', decided_at = expires_at
         WHERE status = 'pending' AND expires_at <= %s
        RETURNING id
    """, (now,))
    return [int(row[0]) for row in cursor.fetchall()]


def mark_issued(cursor, request_id, now):
    """Момент выдачи кода — пишется ПОД блокировкой запроса, до отправки: второе нажатие
    «Код не подошёл» в ту же секунду увидит его и не пришлёт второе сообщение с цифрами."""
    cursor.execute("UPDATE twofa_requests SET last_issued_at = %s WHERE id = %s", (now, int(request_id)))


def code_message_ids(cursor, request_id):
    cursor.execute("SELECT id FROM twofa_messages WHERE request_id = %s AND kind = 'code' "
                   "AND state IN ('shown', 'hidden')", (int(request_id),))
    return [int(row[0]) for row in cursor.fetchall()]


def mark_delivery(cursor, request_id, *, counter=None, error=None):
    """Итог отправки кода: счётчик выданных, шаг последнего кода, ошибка доставки."""
    if error:
        cursor.execute("UPDATE twofa_requests SET delivery_error = %s WHERE id = %s", (error, int(request_id)))
        return
    cursor.execute("""
        UPDATE twofa_requests
           SET codes_sent = codes_sent + 1, last_counter = %s, delivery_error = NULL
         WHERE id = %s
    """, (counter, int(request_id)))


def requests_list(cursor, *, requester_id=None, limit=30):
    where, params = "TRUE", []
    if requester_id is not None:
        where = "r.requester_id = %s"
        params.append(int(requester_id))
    params.append(int(limit))
    cursor.execute("SELECT %s %s WHERE %s ORDER BY r.created_at DESC, r.id DESC LIMIT %%s"
                   % (_REQUEST_COLUMNS, _REQUEST_FROM, where), params)
    return [_request(row) for row in cursor.fetchall()]


def pending_requests(cursor, now):
    cursor.execute("SELECT %s %s WHERE r.status = 'pending' AND r.expires_at > %%s "
                   "ORDER BY r.created_at, r.id" % (_REQUEST_COLUMNS, _REQUEST_FROM), (now,))
    return [_request(row) for row in cursor.fetchall()]


# ─── Сообщения Telegram ────────────────────────────────────────────────────────

def insert_message(cursor, *, request_id, kind, chat_id, message_id, now, hide_at=None, delete_at=None):
    cursor.execute("""
        INSERT INTO twofa_messages (request_id, kind, chat_id, message_id, state, hide_at, delete_at, created_at)
        VALUES (%s, %s, %s, %s, 'shown', %s, %s, %s)
        RETURNING id
    """, (int(request_id), kind, int(chat_id), int(message_id), hide_at, delete_at, now))
    return int(cursor.fetchone()[0])


def claim_messages(cursor, request_id, kind, *, to_state, from_states=('shown', 'hidden')):
    """Перевести сообщения запроса в новое состояние; вернуть те, что перевёл именно этот
    вызов. Второй процесс (перекрытие при выкладке) получит пустой список и ничего не
    отправит повторно."""
    cursor.execute("""
        UPDATE twofa_messages SET state = %s
         WHERE request_id = %s AND kind = %s AND state = ANY(%s)
        RETURNING id, chat_id, message_id
    """, (to_state, int(request_id), kind, list(from_states)))
    return [{'id': int(r[0]), 'chat_id': int(r[1]), 'message_id': int(r[2])} for r in cursor.fetchall()]


def claim_message_ids(cursor, ids, *, to_state):
    """Перевести названные сообщения; вернуть те, что перевёл именно этот вызов."""
    if not ids:
        return []
    cursor.execute("""
        UPDATE twofa_messages SET state = %s
         WHERE id = ANY(%s) AND state IN ('shown', 'hidden')
        RETURNING id, chat_id, message_id
    """, (to_state, [int(x) for x in ids]))
    return [{'id': int(r[0]), 'chat_id': int(r[1]), 'message_id': int(r[2])} for r in cursor.fetchall()]


def claim_due_hide(cursor, now):
    """Пора стереть цифры. Если окно «Код не подошёл» уже закрыто, кнопка не нужна — сообщение
    сразу уходит в 'deleted' (одно удаление вместо «стереть», а следом «удалить»)."""
    cursor.execute("""
        UPDATE twofa_messages m
           SET state = CASE WHEN r.resend_until IS NOT NULL AND r.resend_until > %(now)s
                            THEN 'hidden' ELSE 'deleted' END
          FROM twofa_requests r, twofa_accounts a
         WHERE m.state = 'shown' AND m.hide_at IS NOT NULL AND m.hide_at <= %(now)s
           AND r.id = m.request_id AND a.id = r.account_id
        RETURNING m.id, m.request_id, m.chat_id, m.message_id, r.resend_until, a.title, m.state
    """, {'now': now})
    return [{'id': int(r[0]), 'request_id': int(r[1]), 'chat_id': int(r[2]), 'message_id': int(r[3]),
             'resend_until': r[4], 'account_title': r[5], 'state': r[6]} for r in cursor.fetchall()]


def retry_hide(cursor, message_id, due):
    """Telegram не стёр цифры — вернуть сообщение в очередь «стереть» на due (не больше
    MAX_MESSAGE_ATTEMPTS раз; дальше его уберёт срок удаления)."""
    cursor.execute("""
        UPDATE twofa_messages SET state = 'shown', hide_at = %s, attempts = attempts + 1
         WHERE id = %s AND state = 'hidden' AND attempts < %s
    """, (due, int(message_id), MAX_MESSAGE_ATTEMPTS))


def retry_delete(cursor, message_id, due):
    """Telegram не удалил сообщение — повторить на due (не больше MAX_MESSAGE_ATTEMPTS раз)."""
    cursor.execute("""
        UPDATE twofa_messages SET state = 'hidden', delete_at = %s, attempts = attempts + 1
         WHERE id = %s AND state = 'deleted' AND attempts < %s
    """, (due, int(message_id), MAX_MESSAGE_ATTEMPTS))


def claim_due_delete(cursor, now):
    cursor.execute("""
        UPDATE twofa_messages SET state = 'deleted'
         WHERE state IN ('shown', 'hidden') AND delete_at IS NOT NULL AND delete_at <= %s
        RETURNING id, chat_id, message_id
    """, (now,))
    return [{'id': int(r[0]), 'chat_id': int(r[1]), 'message_id': int(r[2])} for r in cursor.fetchall()]


def next_due(cursor):
    """Ближайший срок, к которому будильнику есть что делать. None — впереди ничего."""
    cursor.execute("""
        SELECT LEAST(
            (SELECT MIN(expires_at) FROM twofa_requests WHERE status = 'pending'),
            (SELECT MIN(hide_at) FROM twofa_messages WHERE state = 'shown' AND hide_at IS NOT NULL),
            (SELECT MIN(delete_at) FROM twofa_messages
              WHERE state IN ('shown', 'hidden') AND delete_at IS NOT NULL)
        )
    """)
    row = cursor.fetchone()
    return row[0] if row else None


# ─── Журнал ────────────────────────────────────────────────────────────────────

def log_event(cursor, kind, *, now, request_id=None, account_id=None, account_title=None,
              actor_id=None, actor_name=None, subject_id=None, subject_name=None, details=None):
    from psycopg2.extras import Json

    cursor.execute("""
        INSERT INTO twofa_events (kind, request_id, account_id, account_title, actor_id, actor_name,
                                  subject_id, subject_name, details, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (kind, request_id, account_id, account_title, actor_id, actor_name, subject_id, subject_name,
          Json(details or {}), now))


def journal(cursor, *, before_id=None, limit=50):
    params = []
    where = "TRUE"
    if before_id:
        where = "e.id < %s"
        params.append(int(before_id))
    params.append(int(limit) + 1)
    cursor.execute("""
        SELECT e.id, e.kind, e.request_id, e.account_id, e.account_title, e.actor_id, e.actor_name,
               e.subject_id, e.subject_name, e.details, e.created_at
          FROM twofa_events e
         WHERE %s
         ORDER BY e.id DESC
         LIMIT %%s
    """ % where, params)
    rows = cursor.fetchall()
    items = [{
        'id': int(r[0]), 'kind': r[1], 'request_id': r[2], 'account_id': r[3], 'account_title': r[4],
        'actor_id': r[5], 'actor_name': r[6], 'subject_id': r[7], 'subject_name': r[8],
        'details': r[9] or {}, 'created_at': r[10],
    } for r in rows[:limit]]
    return items, len(rows) > limit


# ─── Колокол ───────────────────────────────────────────────────────────────────

def bell(cursor, viewer_id, now, limit):
    """Ждущие запросы — главе маркетинга, и только ему: подтверждает он один. И только пока ключ
    шифрования годен: без него раздел главе закрыт (access.can_approve), и строка звала бы туда,
    куда его не пустят. Сгорание строку гасит само: будильник закрывает запрос, триггер будит."""
    if crypto.key_status() != 'ok':
        return 0, []
    lead = approver(cursor)
    if not lead or int(lead['id']) != int(viewer_id):
        return 0, []
    rows = pending_requests(cursor, now)
    return len(rows), rows[:max(0, int(limit))]
