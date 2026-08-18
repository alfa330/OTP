"""SQL раздела «Ограничитель Перезвона».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют ни
транзакцией, ни соединением — как в crm/queries.py.

Логин агента = SIP-номер сотрудника (`users.sip_number`), своего справочника
логинов раздел не заводит.
"""

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Поля настроек, которые вообще можно менять из интерфейса. Всё остальное,
# что придёт в запросе, игнорируется: whitelist, а не «сохрани что прислали».
SETTINGS_FIELDS = (
    'enabled', 'dry_run', 'oktell_url', 'cert_spki', 'threshold_s',
    'warn_before_s', 'recall_reason_id', 'call_state_strings', 'heartbeat_interval_s',
)

DEFAULT_CALL_STATES = ["talk", "dial", "call", "ring"]



def _columns(cursor):
    return [column[0] for column in (cursor.description or [])]


def row_to_dict(cursor, row):
    """Строка курсора → словарь по именам столбцов.

    Курсор проекта возвращает кортежи (conn.cursor() без cursor_factory), и
    dict(row) на них падает. Разбирать по индексам, как в crm, не хочется:
    вставка столбца в середину SELECT молча сдвинула бы все поля.
    """
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    return dict(zip(_columns(cursor), row))


def fetch_one(cursor):
    return row_to_dict(cursor, cursor.fetchone())


def fetch_all(cursor):
    columns = _columns(cursor)
    return [row if isinstance(row, dict) else dict(zip(columns, row)) for row in cursor.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Чистая логика (тестируется без базы)
# ─────────────────────────────────────────────────────────────────────────────

def clamp_threshold(value, default=180):
    """Порог в секундах. Границы жёсткие: 30 секунд — минимум, за которым
    ограничитель превращается в дёрганье людей, 3600 — верх, дальше он просто
    не работает. Мусор и пустое значение = «как у всех»."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(30, min(3600, number))


def effective_rule(settings: dict, personal: dict | None) -> dict:
    """Итоговое правило для конкретного сотрудника.

    Персональный порог перекрывает общий; пустой персональный (NULL) означает
    «как у всех» — именно поэтому в таблице он nullable, а не 0.
    Выключенный сотрудник получает enabled=false и правило в окне не работает.
    """
    settings = settings or {}
    personal = personal or {}
    threshold = personal.get('threshold_s')
    if threshold in (None, ''):
        threshold = settings.get('threshold_s')
    enabled = bool(settings.get('enabled')) and bool(personal.get('enabled', True))
    states = settings.get('call_state_strings') or DEFAULT_CALL_STATES
    return {
        'enabled': enabled,
        'threshold_s': clamp_threshold(threshold),
        'warn_before_s': max(0, int(settings.get('warn_before_s') or 30)),
        'recall_lunch_reason_id': int(settings.get('recall_reason_id') or 2),
        'call_state_strings': list(states),
        'message': f"«Перезвон» дольше {clamp_threshold(threshold) // 60} мин — сессия будет закрыта",
    }


def agent_config_payload(settings: dict, personal: dict | None) -> dict:
    """То, что уезжает агенту в ответ на /config."""
    settings = settings or {}
    rule = effective_rule(settings, personal)
    extra_args = []
    spki = str(settings.get('cert_spki') or '').strip()
    if spki:
        extra_args.append(f"--ignore-certificate-errors-spki-list={spki}")
    return {
        'oktell_url': settings.get('oktell_url') or '',
        'in_window_rule': rule,
        'poll_interval_s': int(settings.get('heartbeat_interval_s') or 60),
        'dry_run': bool(settings.get('dry_run')),
        # keep_open НЕ навязываем: закрыл окно — значит закрыл. Возвращать его
        # силой означает спорить с человеком, а открыть заново он может ярлыком.
        'browser': {'extra_args': extra_args, 'keep_open': False, 'launch_on_start': False},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Настройки
# ─────────────────────────────────────────────────────────────────────────────

def get_settings(cursor) -> dict:
    cursor.execute("""
        SELECT id, enabled, dry_run, oktell_url, cert_spki, threshold_s, warn_before_s,
               recall_reason_id, call_state_strings, heartbeat_interval_s, updated_by, updated_at
          FROM oktell_guard_settings WHERE id = 1
    """)
    return fetch_one(cursor) or {}


def save_settings(cursor, payload: dict, updated_by=None) -> dict:
    fields, values = [], {}
    for name in SETTINGS_FIELDS:
        if name not in payload:
            continue
        value = payload[name]
        if name in ('threshold_s',):
            value = clamp_threshold(value)
        elif name in ('warn_before_s', 'recall_reason_id', 'heartbeat_interval_s'):
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
        elif name in ('enabled', 'dry_run'):
            value = bool(value)
        fields.append(f"{name} = %({name})s")
        values[name] = value
    if not fields:
        return get_settings(cursor)
    values['updated_by'] = updated_by
    cursor.execute(
        f"UPDATE oktell_guard_settings SET {', '.join(fields)}, "
        f"updated_by = %(updated_by)s, updated_at = {_NOW} WHERE id = 1",
        values,
    )
    return get_settings(cursor)


# ─────────────────────────────────────────────────────────────────────────────
# Сотрудники и персональные пороги
# ─────────────────────────────────────────────────────────────────────────────

# Кто попадает в список. Роли и статусы — те же, что в разделе «Настройки SIP»:
# там этот отбор давно работает на живых данных. На users.is_active опираться
# нельзя: колонка по умолчанию FALSE и означает не «работает у нас», из-за чего
# из списка выпадало большинство сотрудников.
EMPLOYEE_ROLES = ('operator', 'trainee')
INACTIVE_STATUSES = ('fired', 'dismissal')

_EMPLOYEES_SQL = """
    SELECT u.id,
           u.name,
           LOWER(COALESCE(u.role, ''))           AS role,
           COALESCE(u.sip_number, '')            AS sip_number,
           d.code                                AS department_code,
           d.name                                AS department_name,
           r.threshold_s                         AS personal_threshold_s,
           COALESCE(r.enabled, TRUE)             AS rule_enabled,
           a.last_seen_at                        AS agent_seen_at,
           a.agent_version                       AS agent_version,
           a.managed_window                      AS agent_window,
           COALESCE(v.kicks, 0)                  AS kicks_30d,
           (m.day IS NOT NULL)                   AS managed_today
      FROM users u
      LEFT JOIN departments d ON d.id = u.department_id
      LEFT JOIN oktell_guard_user_rules r ON r.user_id = u.id
      LEFT JOIN oktell_guard_agents a ON a.user_id = u.id
      LEFT JOIN oktell_guard_managed_days m
             ON m.user_id = u.id
            AND m.day = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')::date
      LEFT JOIN (
            SELECT user_id, COUNT(*) AS kicks
              FROM oktell_guard_violations
             WHERE happened_at >= %(since)s AND NOT dry_run AND verified = 'confirmed'
             GROUP BY user_id
      ) v ON v.user_id = u.id
     WHERE LOWER(COALESCE(u.role, '')) = ANY(%(roles)s)
       AND LOWER(COALESCE(u.status, '')) <> ALL(%(inactive)s)
       AND (%(department_code)s IS NULL OR d.code = %(department_code)s)
     ORDER BY u.name
"""


def list_employees(cursor, department_code=None, since=None):
    """Операторы отдела — все, а не только те, у кого заполнен SIP-номер.

    Сотрудник без номера как раз и есть тот, кого ограничитель не покрывает:
    прятать его из списка означало бы прятать проблему. В интерфейсе он виден
    с пометкой «нет SIP-номера».

    department_code=None — все отделы (глобальный админ), иначе периметр отдела.
    """
    cursor.execute(_EMPLOYEES_SQL, {
        'department_code': department_code,
        'since': since,
        'roles': list(EMPLOYEE_ROLES),
        'inactive': list(INACTIVE_STATUSES),
    })
    return fetch_all(cursor)


def bulk_set_rules(cursor, user_ids, *, threshold_s=None, enabled=None, updated_by=None) -> int:
    """Массовое изменение — то самое «выделил нескольких и поменял».

    threshold_s=None означает «не трогать», а сброс к общему порогу делается
    явным значением 'default' (иначе «не трогать» и «сбросить» неразличимы).
    """
    ids = [int(x) for x in (user_ids or []) if str(x).strip().isdigit()]
    if not ids:
        return 0
    if threshold_s == 'default':
        threshold_value = None
    elif threshold_s is None:
        threshold_value = 'keep'
    else:
        threshold_value = clamp_threshold(threshold_s)

    cursor.execute(
        """
        INSERT INTO oktell_guard_user_rules (user_id, threshold_s, enabled, updated_by, updated_at)
        SELECT uid,
               CASE WHEN %(threshold_keep)s THEN NULL ELSE %(threshold)s END,
               COALESCE(%(enabled)s, TRUE),
               %(updated_by)s,
               """ + _NOW + """
          FROM UNNEST(%(ids)s::int[]) AS uid
        ON CONFLICT (user_id) DO UPDATE SET
               threshold_s = CASE WHEN %(threshold_keep)s
                                  THEN oktell_guard_user_rules.threshold_s
                                  ELSE %(threshold)s END,
               enabled     = COALESCE(%(enabled)s, oktell_guard_user_rules.enabled),
               updated_by  = %(updated_by)s,
               updated_at  = """ + _NOW,
        {
            'ids': ids,
            'threshold': None if threshold_value in ('keep', None) else threshold_value,
            'threshold_keep': threshold_value == 'keep',
            'enabled': enabled,
            'updated_by': updated_by,
        },
    )
    return len(ids)


def personal_rule_by_sip(cursor, sip_number: str):
    """Персональные настройки по SIP-номеру — так агент себя и представляет."""
    sip = str(sip_number or '').strip()
    if not sip:
        return None
    cursor.execute(
        """
        SELECT u.id AS user_id, u.name, r.threshold_s, COALESCE(r.enabled, TRUE) AS enabled
          FROM users u
          LEFT JOIN oktell_guard_user_rules r ON r.user_id = u.id
         WHERE COALESCE(u.sip_number, '') = %(sip)s
           AND LOWER(COALESCE(u.status, '')) <> ALL(%(inactive)s)
         LIMIT 1
        """,
        {'sip': sip, 'inactive': list(INACTIVE_STATUSES)},
    )
    return fetch_one(cursor)


# ─────────────────────────────────────────────────────────────────────────────
# Журнал выбросов и живость агентов
# ─────────────────────────────────────────────────────────────────────────────

def record_violation(cursor, payload: dict) -> bool:
    """Записать выброс. Возвращает False, если такой уже был (client_key).

    Идемпотентность нужна не для красоты: агент повторяет отправку при обрыве
    связи, и один выброс не должен превратиться в три строки отчёта.
    """
    cursor.execute(
        """
        INSERT INTO oktell_guard_violations
               (user_id, sip_number, happened_at, seconds, threshold_s, reason,
                hostname, windows_user, agent_version, dry_run, client_key,
                verified, verified_note, reported_by)
        VALUES (%(user_id)s, %(sip_number)s, COALESCE(%(happened_at)s, """ + _NOW + """),
                %(seconds)s, %(threshold_s)s, %(reason)s, %(hostname)s, %(windows_user)s,
                %(agent_version)s, %(dry_run)s, %(client_key)s,
                COALESCE(%(verified)s, 'pending'), COALESCE(%(verified_note)s, ''),
                %(reported_by)s)
        ON CONFLICT (client_key) WHERE client_key <> '' DO NOTHING
        RETURNING id
        """,
        payload,
    )
    return cursor.fetchone() is not None


def upsert_agent(cursor, payload: dict) -> None:
    cursor.execute(
        """
        INSERT INTO oktell_guard_agents
               (agent_id, user_id, sip_number, hostname, windows_user, agent_version,
                managed_window, session_present, unmanaged_count, last_seen_at)
        VALUES (%(agent_id)s, %(user_id)s, %(sip_number)s, %(hostname)s, %(windows_user)s,
                %(agent_version)s, %(managed_window)s, %(session_present)s,
                %(unmanaged_count)s, """ + _NOW + """)
        ON CONFLICT (agent_id) DO UPDATE SET
               user_id = EXCLUDED.user_id,
               sip_number = EXCLUDED.sip_number,
               hostname = EXCLUDED.hostname,
               windows_user = EXCLUDED.windows_user,
               agent_version = EXCLUDED.agent_version,
               managed_window = EXCLUDED.managed_window,
               session_present = EXCLUDED.session_present,
               unmanaged_count = EXCLUDED.unmanaged_count,
               last_seen_at = """ + _NOW,
        payload,
    )


def rejected_count(cursor, date_from, date_to, department_code=None) -> int:
    """Сколько присланных фактов не подтвердилось историей Oktell.

    Показывается рядом с отчётом: ноль — норма, а заметное число означает либо
    расхождение часов на машинах, либо чью-то попытку прислать выдуманное.
    """
    cursor.execute(
        """
        SELECT COUNT(*) AS cnt
          FROM oktell_guard_violations v
          LEFT JOIN users u ON u.id = v.user_id
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE v.happened_at::date BETWEEN %(date_from)s AND %(date_to)s
           AND (%(department_code)s IS NULL OR d.code = %(department_code)s)
           AND v.verified = 'rejected'
        """,
        {'date_from': date_from, 'date_to': date_to, 'department_code': department_code},
    )
    row = fetch_one(cursor) or {}
    return int(row.get('cnt') or 0)


def report(cursor, date_from, date_to, department_code=None):
    """Отчёт «за какую дату сколько раз выкинуло», по сотрудникам.

    Только подтверждённые историей Oktell записи: программа стоит на компьютере
    сотрудника, и непроверенным её словам в отчёте не место.
    """
    cursor.execute(
        """
        SELECT v.user_id,
               COALESCE(u.name, '(неизвестный)') AS name,
               v.sip_number,
               d.code AS department_code,
               v.happened_at::date AS day,
               COUNT(*) AS kicks,
               MAX(v.seconds) AS max_seconds,
               BOOL_OR(v.dry_run) AS had_dry_run
          FROM oktell_guard_violations v
          LEFT JOIN users u ON u.id = v.user_id
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE v.happened_at::date BETWEEN %(date_from)s AND %(date_to)s
           AND (%(department_code)s IS NULL OR d.code = %(department_code)s)
           AND v.verified = 'confirmed'
         GROUP BY v.user_id, u.name, v.sip_number, d.code, v.happened_at::date
         ORDER BY day DESC, kicks DESC, name
        """,
        {'date_from': date_from, 'date_to': date_to, 'department_code': department_code},
    )
    return fetch_all(cursor)


# ─────────────────────────────────────────────────────────────────────────────
# Версии агента
# ─────────────────────────────────────────────────────────────────────────────

def current_release(cursor):
    cursor.execute("""
        SELECT id, version, filename, sha256, size_bytes, gcs_bucket, gcs_path,
               notes, uploaded_by, uploaded_at
          FROM oktell_guard_releases WHERE is_current LIMIT 1
    """)
    return fetch_one(cursor)


def add_release(cursor, *, version, filename, sha256, size_bytes, gcs_bucket, gcs_path,
                notes='', uploaded_by=None):
    """Новая версия становится текущей, прежняя перестаёт ею быть.

    Текущая ровно одна — иначе половина машин обновится не туда.
    """
    cursor.execute("UPDATE oktell_guard_releases SET is_current = FALSE WHERE is_current")
    cursor.execute(
        """
        INSERT INTO oktell_guard_releases
               (version, filename, sha256, size_bytes, gcs_bucket, gcs_path, notes,
                is_current, uploaded_by, uploaded_at)
        VALUES (%(version)s, %(filename)s, %(sha256)s, %(size_bytes)s, %(gcs_bucket)s,
                %(gcs_path)s, %(notes)s, TRUE, %(uploaded_by)s, """ + _NOW + """)
        ON CONFLICT (version) DO UPDATE SET
               filename = EXCLUDED.filename,
               sha256 = EXCLUDED.sha256,
               size_bytes = EXCLUDED.size_bytes,
               gcs_bucket = EXCLUDED.gcs_bucket,
               gcs_path = EXCLUDED.gcs_path,
               notes = EXCLUDED.notes,
               is_current = TRUE,
               uploaded_by = EXCLUDED.uploaded_by,
               uploaded_at = """ + _NOW + """
        RETURNING id
        """,
        {
            'version': version, 'filename': filename, 'sha256': sha256,
            'size_bytes': size_bytes, 'gcs_bucket': gcs_bucket, 'gcs_path': gcs_path,
            'notes': notes, 'uploaded_by': uploaded_by,
        },
    )
    row = cursor.fetchone()
    return row[0] if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Личные токены и пометка «работал через наше приложение»
# ─────────────────────────────────────────────────────────────────────────────

def issue_token(cursor, user_id: int, token_hash: str, note: str = '') -> None:
    """Выдать сотруднику личный токен (храним только отпечаток).

    Прежние его токены гасим: у человека один действующий, иначе отозвать
    скомпрометированный означало бы гадать, каким из пяти он пользуется.
    """
    cursor.execute(
        "UPDATE oktell_guard_tokens SET revoked_at = " + _NOW +
        " WHERE user_id = %(user_id)s AND revoked_at IS NULL",
        {'user_id': int(user_id)},
    )
    cursor.execute(
        """
        INSERT INTO oktell_guard_tokens (user_id, token_hash, note)
        VALUES (%(user_id)s, %(token_hash)s, %(note)s)
        ON CONFLICT (token_hash) DO UPDATE SET revoked_at = NULL
        """,
        {'user_id': int(user_id), 'token_hash': token_hash, 'note': note},
    )


def user_by_token(cursor, token_hash: str):
    """Кому принадлежит присланный токен. Отозванные не в счёт."""
    if not token_hash:
        return None
    cursor.execute(
        """
        SELECT t.id, t.user_id, u.name, COALESCE(u.sip_number, '') AS sip_number
          FROM oktell_guard_tokens t
          JOIN users u ON u.id = t.user_id
         WHERE t.token_hash = %(token_hash)s AND t.revoked_at IS NULL
         LIMIT 1
        """,
        {'token_hash': token_hash},
    )
    found = fetch_one(cursor)
    if not found:
        return None
    cursor.execute(
        "UPDATE oktell_guard_tokens SET last_used_at = " + _NOW + " WHERE id = %(id)s",
        {'id': found['id']},
    )
    return found


def mark_managed_day(cursor, user_id) -> None:
    """Отметить, что сегодня человек работал в Oktell через наше приложение.

    Пометка, а не запрет: сейчас она просто видна в разделе. Решение
    «нет пометки — смена не засчитана» принимается отдельно и позже.
    """
    if not user_id:
        return
    cursor.execute(
        """
        INSERT INTO oktell_guard_managed_days (user_id, day, first_seen_at, last_seen_at, samples)
        VALUES (%(user_id)s, (""" + _NOW + """)::date, """ + _NOW + """, """ + _NOW + """, 1)
        ON CONFLICT (user_id, day) DO UPDATE SET
               last_seen_at = """ + _NOW + """,
               samples = oktell_guard_managed_days.samples + 1
        """,
        {'user_id': int(user_id)},
    )


def managed_days(cursor, date_from, date_to, department_code=None):
    cursor.execute(
        """
        SELECT m.user_id, u.name, m.day, m.first_seen_at, m.last_seen_at, m.samples
          FROM oktell_guard_managed_days m
          JOIN users u ON u.id = m.user_id
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE m.day BETWEEN %(date_from)s AND %(date_to)s
           AND (%(department_code)s IS NULL OR d.code = %(department_code)s)
         ORDER BY m.day DESC, u.name
        """,
        {'date_from': date_from, 'date_to': date_to, 'department_code': department_code},
    )
    return fetch_all(cursor)


def access_context(cursor, user_id):
    """Кто пришёл: роль, отдел и возглавляет ли он отдел.

    Отдельным запросом, а не разбором кортежа из _resolve_requester: там
    пользователь приходит СТРОКОЙ базы, и обращение к ней по имени поля молча
    давало None — из-за этого раздел закрывался даже суперадмину. Порядок
    столбцов в той строке меняется вместе с чужими правками, привязываться к
    нему нельзя.
    """
    if not user_id:
        return None
    cursor.execute(
        """
        SELECT u.id,
               u.name,
               u.role,
               COALESCE(d.code, '')  AS department_code,
               EXISTS (
                   SELECT 1 FROM departments h
                    WHERE h.head_user_id = u.id AND h.is_active
               )                     AS is_department_head,
               COALESCE((
                   SELECT h.code FROM departments h
                    WHERE h.head_user_id = u.id AND h.is_active
                    LIMIT 1
               ), '')                AS headed_department_code
          FROM users u
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE u.id = %(user_id)s
        """,
        {'user_id': int(user_id)},
    )
    ctx = fetch_one(cursor)
    if not ctx:
        return None
    # Глава отдела считается по отделу, которым он РУКОВОДИТ: его собственный
    # department_id может быть не заполнен или указывать на другой отдел.
    if ctx.get('is_department_head') and ctx.get('headed_department_code'):
        ctx['department_code'] = ctx['headed_department_code']
    return ctx
