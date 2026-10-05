"""SQL раздела «Ограничитель Перезвона».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют ни
транзакцией, ни соединением — как в crm/queries.py.

Логин агента = логин человека в Oktell, и ищется он ТОЛЬКО среди сотрудников
СЗоВ: кабинет из «Настроек SIP», а без кабинета — `users.sip_number`
(см. OKTELL_LOGIN_SQL).

Отдел продаж (iCORE Phone) живёт в своих таблицах oktell_guard_phone_*: его
запросы ниже, в разделе «Отдел продаж», и Oktell-запросов не касаются.
"""

import json

from . import phone

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Поля настроек, которые вообще можно менять из интерфейса. Всё остальное,
# что придёт в запросе, игнорируется: whitelist, а не «сохрани что прислали».
SETTINGS_FIELDS = (
    'enabled', 'dry_run', 'oktell_url', 'cert_spki', 'threshold_s',
    'warn_before_s', 'recall_reason_id', 'call_state_strings', 'call_state_ids',
    'heartbeat_interval_s',
)

# usFullbusy (код 5) — состояние разговора в Oktell, проверено на живых звонках.
DEFAULT_CALL_STATES = ["fullbusy", "talk", "dial", "call", "ring"]
DEFAULT_CALL_STATE_IDS = [5]



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
    state_ids = settings.get('call_state_ids') or DEFAULT_CALL_STATE_IDS
    return {
        'enabled': enabled,
        'threshold_s': clamp_threshold(threshold),
        'warn_before_s': max(0, int(settings.get('warn_before_s') or 30)),
        'recall_lunch_reason_id': int(settings.get('recall_reason_id') or 2),
        'call_state_strings': list(states),
        'call_state_ids': list(state_ids),
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
               recall_reason_id, call_state_strings, call_state_ids, heartbeat_interval_s,
               updated_by, updated_at
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

# Логин человека в Oktell. Решение владельца 24.09.2026: «не перемешивай SIP-номера
# с другими разделами, только раздел СЗоВ — чтобы для Oktell подтягивалось только с
# него». В Oktell работает один отдел, а номера разных АТС совпадают: логин Oktell
# оператора СЗоВ и номер своей АТС у сотрудника ОП бывают одним и тем же числом.
# Поиск по всей компании отдавал машину СЗоВ чужому человеку, в том числе
# уволенному, и туда же уходили его выбросы.
OKTELL_DEPARTMENT_CODE = 'szov'

# Кабинет — первым: ровно этим логином агент входит в Oktell (/config) и ровно его
# присылает обратно (cookie __oktelllogin). С 21.09 карточка СЗоВ ведёт только
# кабинет, а users.sip_number при сохранении не трогает — у 21 человека он пуст,
# у одного устарел. users.sip_number — запасной, пока кабинет не завели: до 21.09
# логином был он. Требует `u` = users и LEFT JOIN `oka` = oktell_user_accounts.
OKTELL_LOGIN_SQL = ("COALESCE(NULLIF(btrim(oka.cabinet_login), ''), "
                    "NULLIF(btrim(u.sip_number), ''))")

_EMPLOYEES_SQL = """
    SELECT u.id,
           u.name,
           LOWER(COALESCE(u.role, ''))           AS role,
           -- Ключ прежний (sip_number), значение — логин Oktell: вкладка ищет и
           -- помечает «нет SIP-номера» по нему. Вне СЗоВ логина Oktell нет.
           CASE WHEN d.code = %(oktell_department)s
                THEN COALESCE(""" + OKTELL_LOGIN_SQL + """, '')
                ELSE '' END                      AS sip_number,
           d.code                                AS department_code,
           d.name                                AS department_name,
           r.threshold_s                         AS personal_threshold_s,
           COALESCE(r.enabled, TRUE)             AS rule_enabled,
           a.last_seen_at                        AS agent_seen_at,
           a.agent_version                       AS agent_version,
           a.managed_window                      AS agent_window,
           a.session_present                     AS agent_session,
           a.unmanaged_count                     AS unmanaged_count,
           -- «Правило считает» — не то же самое, что «агент жив»: правило
           -- живёт в окне и может стоять там слепым. Без этой колонки раздел
           -- показывал зелёного агента у человека, которого ничто не ограничивает.
           a.rule_alive                          AS rule_alive,
           a.rule_seconds                        AS rule_seconds,
           COALESCE(v.kicks, 0)                  AS kicks_30d,
           -- Пересидел, а ограничитель не сработал: число, ради которого
           -- серверная сверка и написана. С выбросами не складывается.
           COALESCE(v.missed, 0)                 AS missed_30d,
           (m.day IS NOT NULL)                   AS managed_today
      FROM users u
      LEFT JOIN departments d ON d.id = u.department_id
      LEFT JOIN oktell_user_accounts oka ON oka.user_id = u.id
      LEFT JOIN oktell_guard_user_rules r ON r.user_id = u.id
      LEFT JOIN oktell_guard_agents a ON a.user_id = u.id
      LEFT JOIN oktell_guard_managed_days m
             ON m.user_id = u.id
            AND m.day = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')::date
      LEFT JOIN (
            SELECT user_id,
                   COUNT(*) FILTER (WHERE reason <> 'recall_unmanaged') AS kicks,
                   COUNT(*) FILTER (WHERE reason = 'recall_unmanaged') AS missed
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
        'oktell_department': OKTELL_DEPARTMENT_CODE,
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
    """Персональные настройки по логину Oktell — так агент себя и представляет.

    Только СЗоВ (OKTELL_LOGIN_SQL). Логин на двух действующих людях — никто: как
    и в thresholds_by_sip, машину и выбросы наугад не приписываем. Раньше здесь
    стоял LIMIT 1 без порядка, и такой номер доставался случайному из двух.
    """
    sip = str(sip_number or '').strip()
    if not sip:
        return None
    cursor.execute(
        """
        SELECT u.id AS user_id, u.name, r.threshold_s, COALESCE(r.enabled, TRUE) AS enabled
          FROM users u
          JOIN departments d ON d.id = u.department_id
          LEFT JOIN oktell_user_accounts oka ON oka.user_id = u.id
          LEFT JOIN oktell_guard_user_rules r ON r.user_id = u.id
         WHERE d.code = %(oktell_department)s
           AND """ + OKTELL_LOGIN_SQL + """ = %(sip)s
           AND LOWER(COALESCE(u.status, '')) <> ALL(%(inactive)s)
         ORDER BY u.id
         LIMIT 2
        """,
        {'sip': sip, 'inactive': list(INACTIVE_STATUSES),
         'oktell_department': OKTELL_DEPARTMENT_CODE},
    )
    rows = fetch_all(cursor)
    return rows[0] if len(rows) == 1 else None


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
                managed_window, session_present, unmanaged_count,
                rule_alive, rule_sockets, rule_seconds, rule_version, last_seen_at)
        VALUES (%(agent_id)s, %(user_id)s, %(sip_number)s, %(hostname)s, %(windows_user)s,
                %(agent_version)s, %(managed_window)s, %(session_present)s,
                %(unmanaged_count)s, %(rule_alive)s, %(rule_sockets)s, %(rule_seconds)s,
                %(rule_version)s, """ + _NOW + """)
        ON CONFLICT (agent_id) DO UPDATE SET
               -- Логин агент знает только из живой вкладки. Закрыл человек окно —
               -- heartbeat приходит без логина, и прежняя запись затиралась в NULL:
               -- раздел показывал «Не установлен» у машины, которая отмечается
               -- каждую минуту (именно это и было видно в проде 04.09).
               -- Держим последнее известное, пока не придёт новое.
               user_id = COALESCE(EXCLUDED.user_id, oktell_guard_agents.user_id),
               sip_number = CASE WHEN COALESCE(EXCLUDED.sip_number, '') = ''
                                 THEN oktell_guard_agents.sip_number
                                 ELSE EXCLUDED.sip_number END,
               hostname = EXCLUDED.hostname,
               windows_user = EXCLUDED.windows_user,
               agent_version = EXCLUDED.agent_version,
               managed_window = EXCLUDED.managed_window,
               session_present = EXCLUDED.session_present,
               unmanaged_count = EXCLUDED.unmanaged_count,
               rule_alive = EXCLUDED.rule_alive,
               rule_sockets = EXCLUDED.rule_sockets,
               rule_seconds = EXCLUDED.rule_seconds,
               rule_version = EXCLUDED.rule_version,
               last_seen_at = """ + _NOW,
        payload,
    )


def thresholds_by_sip(cursor):
    """Кого касается правило: ({логин: {user_id, threshold_s}}, {спорный логин: [id]}).

    В первый словарь попадают только те, кого правило касается: действующие
    операторы СЗоВ с логином Oktell (OKTELL_LOGIN_SQL) и не выключенные лично.
    Остальные из сверки выпадают целиком — иначе она нашла бы «нарушения» у тех,
    к кому ограничитель не применяется вовсе. Отдел не параметр: раньше сверку
    звали без него, и история Oktell сверялась с номерами всей компании — чужой
    номер ОП забирал себе «Перезвон» оператора СЗоВ.

    Второй словарь — логины, висящие сразу на нескольких действующих людях.
    Это не ошибка сверки, а расхождение в справочнике, и чинить его надо там.
    """
    cursor.execute(
        """
        SELECT """ + OKTELL_LOGIN_SQL + """                  AS sip_number,
               u.id                                          AS user_id,
               COALESCE(r.threshold_s, s.threshold_s, 180)   AS threshold_s,
               (COALESCE(r.enabled, TRUE) AND s.enabled)     AS enabled
          FROM users u
          JOIN departments d ON d.id = u.department_id
          LEFT JOIN oktell_user_accounts oka ON oka.user_id = u.id
          LEFT JOIN oktell_guard_user_rules r ON r.user_id = u.id
          CROSS JOIN oktell_guard_settings s
         WHERE s.id = 1
           AND d.code = %(oktell_department)s
           AND LOWER(COALESCE(u.role, '')) = ANY(%(roles)s)
           AND LOWER(COALESCE(u.status, '')) <> ALL(%(inactive)s)
           AND """ + OKTELL_LOGIN_SQL + """ IS NOT NULL
        """,
        {'roles': list(EMPLOYEE_ROLES), 'inactive': list(INACTIVE_STATUSES),
         'oktell_department': OKTELL_DEPARTMENT_CODE},
    )
    out, seen = {}, {}
    for row in fetch_all(cursor):
        sip = str(row['sip_number'])
        seen.setdefault(sip, []).append(row['user_id'])
        if not row.get('enabled'):
            continue
        out[sip] = {
            'user_id': row['user_id'],
            'threshold_s': int(row['threshold_s'] or 180),
        }
    # Один SIP-номер на двух действующих сотрудников — в проде такое есть
    # (04.09.2026: 6684, 6674, 6648, 6638). По номеру человека тогда не
    # опознать, а записать выброс наугад значит обвинить, может быть, невиновного.
    # Такие номера из сверки исключаем целиком и говорим о них вслух.
    ambiguous = {sip: sorted(ids) for sip, ids in seen.items() if len(ids) > 1}
    for sip in ambiguous:
        out.pop(sip, None)
    return out, ambiguous


def pending_violations(cursor, since, limit=200):
    """Факты, которые не удалось сверить с Oktell в момент получения.

    Прокси к базе АТС падает и поднимается сам по себе, и в такие минуты сверка
    честно ставит 'pending'. Беда была в том, что это состояние КОНЕЧНОЕ: никто
    не возвращался к записи, а отчёт показывает только подтверждённое. Реальный
    выброс так и оставался невидимым — «выкидываний не отображается».
    """
    cursor.execute(
        """
        SELECT id, user_id, sip_number, happened_at, seconds, threshold_s, reason
          FROM oktell_guard_violations
         WHERE verified = 'pending'
           AND happened_at >= %(since)s
           AND COALESCE(sip_number, '') <> ''
         ORDER BY happened_at DESC
         LIMIT %(limit)s
        """,
        {'since': since, 'limit': int(limit)},
    )
    return fetch_all(cursor)


def set_violation_verdict(cursor, violation_id, status, note) -> None:
    cursor.execute(
        """
        UPDATE oktell_guard_violations
           SET verified = %(status)s, verified_note = %(note)s
         WHERE id = %(id)s AND verified = 'pending'
        """,
        {'id': int(violation_id), 'status': str(status)[:16], 'note': str(note or '')},
    )


def pending_count(cursor, date_from, date_to, department_code=None) -> int:
    """Сколько фактов ждут сверки. Ноль — норма; заметное число означает, что
    прокси к Oktell лежит, и часть выбросов пока не показана."""
    cursor.execute(
        """
        SELECT COUNT(*) AS cnt
          FROM oktell_guard_violations v
          LEFT JOIN users u ON u.id = v.user_id
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE v.happened_at::date BETWEEN %(date_from)s AND %(date_to)s
           AND (%(department_code)s IS NULL OR d.code = %(department_code)s)
           AND v.verified = 'pending'
        """,
        {'date_from': date_from, 'date_to': date_to, 'department_code': department_code},
    )
    row = fetch_one(cursor) or {}
    return int(row.get('cnt') or 0)


def violations_between(cursor, since, until):
    """Уже записанные выбросы за период — чтобы сверка не удвоила отчёт.

    Берём и подтверждённые, и отклонённые: отклонённый факт всё равно означает,
    что программа в тот момент работала и о человеке доложила.
    """
    cursor.execute(
        """
        SELECT sip_number, happened_at, reason
          FROM oktell_guard_violations
         WHERE happened_at >= %(since)s AND happened_at < %(until)s
        """,
        {'since': since, 'until': until},
    )
    return fetch_all(cursor)


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

    Выбросы и пересиженное считаются РАЗДЕЛЬНО. Записи серверной сверки
    (reason='recall_unmanaged') означают ровно обратное выбросу: человек
    пересидел, а ограничитель до него не доехал. Сложить их в одно число
    значило бы отчитываться о работе ограничителя его же неудачами.
    """
    cursor.execute(
        """
        SELECT v.user_id,
               COALESCE(u.name, '(неизвестный)') AS name,
               v.sip_number,
               d.code AS department_code,
               v.happened_at::date AS day,
               COUNT(*) FILTER (WHERE v.verified = 'confirmed'
                                  AND v.reason <> 'recall_unmanaged') AS kicks,
               COUNT(*) FILTER (WHERE v.verified = 'confirmed'
                                  AND v.reason = 'recall_unmanaged') AS missed,
               -- Ждущие сверки показываем отдельным числом, а не прячем. Прокси
               -- к базе АТС падает на часы и дни (05–07.09 лежал двое суток), и
               -- всё это время состоявшиеся выбросы выглядели как их отсутствие.
               COUNT(*) FILTER (WHERE v.verified = 'pending') AS pending,
               MAX(v.seconds) AS max_seconds,
               BOOL_OR(v.dry_run) AS had_dry_run
          FROM oktell_guard_violations v
          LEFT JOIN users u ON u.id = v.user_id
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE v.happened_at::date BETWEEN %(date_from)s AND %(date_to)s
           AND (%(department_code)s IS NULL OR d.code = %(department_code)s)
           AND v.verified IN ('confirmed', 'pending')
         GROUP BY v.user_id, u.name, v.sip_number, d.code, v.happened_at::date
         ORDER BY day DESC, kicks DESC, pending DESC, name
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
# Кто за машиной и пометка «работал через наше приложение»
# ─────────────────────────────────────────────────────────────────────────────

def user_brief(cursor, user_id):
    """Имя и логин Oktell вошедшего. Форма та же, что была у user_by_token.

    Логин нужен не для красоты: по нему сверяется, про свой ли номер прислан факт
    выброса. Поэтому брать человека `db.get_user` нельзя — там этой колонки нет.
    Ключ прежний (sip_number), значение — OKTELL_LOGIN_SQL: по users.sip_number
    оператор, у которого кабинет расходится с устаревшим номером, получал на
    каждый свой выброс пометку «агент принадлежит <ему же>, факт про номер …».
    """
    if not user_id:
        return None
    cursor.execute(
        """
        SELECT u.id AS user_id, u.name,
               CASE WHEN d.code = %(oktell_department)s
                    THEN COALESCE(""" + OKTELL_LOGIN_SQL + """, '')
                    ELSE '' END AS sip_number
          FROM users u
          LEFT JOIN departments d ON d.id = u.department_id
          LEFT JOIN oktell_user_accounts oka ON oka.user_id = u.id
         WHERE u.id = %(user_id)s
        """,
        {'user_id': int(user_id), 'oktell_department': OKTELL_DEPARTMENT_CODE},
    )
    return fetch_one(cursor)


def user_by_token(cursor, token_hash: str):
    """Кому принадлежит присланный токен. Отозванные не в счёт.

    Осталось только ради сборок до 1.0.17: они шлют личный токен вместо токена
    машины, и без этой проверки такая машина получала бы 401 до автообновления.
    Новые токены не выдаются — кто за машиной, говорит вход по учётке iCORE.
    Убрать вместе с ветвью в routes.agent_authorized.
    """
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


# ─────────────────────────────────────────────────────────────────────────────
# Отдел продаж: автоофлайн iCORE Phone (ТЗ 05.10.2026)
# ─────────────────────────────────────────────────────────────────────────────
#
# Oktell здесь нет: «выброс» в «Офлайн» делает сам телефон, а сервер получает
# его обычным событием статуса (/api/operator/status_event, см. phone.py) и
# записывает в oktell_guard_phone_kicks. Ни один запрос ниже не читает Oktell-
# таблиц, и ни один Oktell-запрос выше не читает эти — серверная сверка не
# должна видеть строк телефона (почему — в schema.py).

# Отдел продаж узнаём и по id: у части профилей код в справочнике пуст (тот же
# довод и то же число, что у AI_QA_OP_DEPARTMENT_ID в bot_schedule2 и у фронта).
# Только запасной путь — заполненный код решает всегда.
OP_DEPARTMENT_FALLBACK_ID = 367
# Код отдела строки {d} из departments. Требует параметр %(op_department_id)s.
_DEPARTMENT_CODE_SQL = ("LOWER(COALESCE(NULLIF(btrim({d}.code), ''), "
                        "CASE WHEN {d}.id = %(op_department_id)s THEN 'op' END, ''))")


def _json_list(value):
    """JSONB из драйвера — уже список; строкой он приходит, только если колонку
    прочитали как text. Не разобралось — None, то есть «как по умолчанию»."""
    if value is None or isinstance(value, (list, tuple)):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return value
    return parsed if isinstance(parsed, list) else None


def get_phone_settings(cursor, department_code=phone.PHONE_DEPARTMENT_CODE) -> dict:
    """Правило автоофлайна отдела: нормализованное + кто и когда правил.

    Строки нет (схема ещё не развернулась, отдел не засеян) — правило по
    умолчанию: телефон не должен остаться без профиля статусов из-за пустой
    таблицы. Ошибку самой базы не глотаем — это решает вызывающий.
    """
    cursor.execute(
        """
        SELECT s.enabled, s.threshold_s, s.warn_before_s, s.groups,
               s.updated_by, s.updated_at, u.name AS updated_by_name
          FROM oktell_guard_phone_settings s
          LEFT JOIN users u ON u.id = s.updated_by
         WHERE s.department_code = %(department_code)s
        """,
        {'department_code': str(department_code or '').strip().lower()},
    )
    row = fetch_one(cursor) or {}
    rule = phone.normalize_phone_settings({
        'enabled': row.get('enabled'),
        'threshold_s': row.get('threshold_s'),
        'warn_before_s': row.get('warn_before_s'),
        'groups': _json_list(row.get('groups')),
    })
    rule.update({
        'updated_at': row.get('updated_at'),
        'updated_by': row.get('updated_by'),
        'updated_by_name': row.get('updated_by_name'),
    })
    return rule


def save_phone_settings(cursor, department_code, changes, updated_by=None) -> dict:
    """Сохранить правило: присланное поверх текущего, затем нормализация.

    Whitelist (phone.PHONE_SETTINGS_FIELDS), как SETTINGS_FIELDS у Oktell;
    null в запросе = «не трогать». Нормализуется ВСЁ правило, а не только
    присланное: уменьшили порог — предупреждение подрежется под новый порог.

    Присланное проверяется ДО наложения: непригодное значение —
    phone.PhoneSettingsError (ValueError), база не тронута. Нормализация на
    непонятное отвечает значением по умолчанию, и без проверки «выкл» строкой
    включало бы выбросы (см. phone.validate_phone_settings_changes).

    Ничего не изменилось — строку не трогаем: «кто и когда правил» должно
    значить настоящую правку. Форма шлёт поле и при уходе с него без изменений,
    и СВ иначе видел бы «правил глава, только что» у правила, которое никто не
    менял.
    """
    code = str(department_code or '').strip().lower()
    checked = phone.validate_phone_settings_changes(changes)
    current = get_phone_settings(cursor, code)
    merged = {name: current.get(name) for name in phone.PHONE_SETTINGS_FIELDS}
    merged.update(checked)
    rule = phone.normalize_phone_settings(merged)
    if all(rule[name] == current.get(name) for name in phone.PHONE_SETTINGS_FIELDS):
        return current
    cursor.execute(
        """
        INSERT INTO oktell_guard_phone_settings
               (department_code, enabled, threshold_s, warn_before_s, groups,
                updated_by, updated_at)
        VALUES (%(department_code)s, %(enabled)s, %(threshold_s)s, %(warn_before_s)s,
                %(groups)s::jsonb, %(updated_by)s, """ + _NOW + """)
        ON CONFLICT (department_code) DO UPDATE SET
               enabled       = EXCLUDED.enabled,
               threshold_s   = EXCLUDED.threshold_s,
               warn_before_s = EXCLUDED.warn_before_s,
               groups        = EXCLUDED.groups,
               updated_by    = EXCLUDED.updated_by,
               updated_at    = EXCLUDED.updated_at
        """,
        {
            'department_code': code,
            'enabled': rule['enabled'],
            'threshold_s': rule['threshold_s'],
            'warn_before_s': rule['warn_before_s'],
            'groups': json.dumps(rule['groups']),
            'updated_by': updated_by,
        },
    )
    return get_phone_settings(cursor, code)


def record_phone_kick(cursor, *, user_id, happened_at, client_key,
                      department_code=phone.PHONE_DEPARTMENT_CODE, status_group='',
                      threshold_s=0) -> bool:
    """Записать выброс телефона. True — строка добавлена, False — такой уже был.

    Идемпотентность по client_key (phone.kick_client_key): очередь телефона
    повторяет событие при обрыве связи, и один выброс не должен стать двумя.
    Без ключа не пишем вовсе — повтор было бы не отличить от нового выброса.
    """
    key = str(client_key or '').strip()[:128]
    if not key:
        return False
    try:
        threshold = max(0, int(threshold_s or 0))
    except (TypeError, ValueError):
        threshold = 0
    cursor.execute(
        """
        INSERT INTO oktell_guard_phone_kicks
               (user_id, department_code, status_group, happened_at, threshold_s,
                client_key, received_at)
        VALUES (%(user_id)s, %(department_code)s, %(status_group)s,
                COALESCE(%(happened_at)s, """ + _NOW + """), %(threshold_s)s,
                %(client_key)s, """ + _NOW + """)
        ON CONFLICT (client_key) DO NOTHING
        RETURNING id
        """,
        {
            'user_id': int(user_id) if user_id is not None else None,
            'department_code': str(department_code or phone.PHONE_DEPARTMENT_CODE).strip().lower()[:32],
            'status_group': str(status_group or '').strip().lower()[:16],
            'happened_at': happened_at,
            'threshold_s': threshold,
            'client_key': key,
        },
    )
    return cursor.fetchone() is not None


# Группа на дату — та же лестница, что у Database._load_operator_calculation_
# models_tx(as_of=...): действующее на дату членство решает всегда (при
# пересечении — позднее start_date, затем больший id), направление — только без
# него. Дата приходит из Python (Алматы): часы базы в UTC, и CURRENT_DATE до
# 05:00 по Алматы вернул бы вчера.
_PHONE_EMPLOYEES_SQL = """
    SELECT u.id,
           u.name,
           LOWER(COALESCE(u.role, ''))                    AS role,
           COALESCE(btrim(u.sip_number), '')              AS sip_number,
           d.name                                         AS department_name,
           COALESCE(g.group_name, '')                     AS group_name,
           LOWER(COALESCE(g.calculation_model_code, ''))  AS group_model,
           LOWER(COALESCE(dir.calculation_model_code, '')) AS direction_model,
           COALESCE(k.kicks, 0)                           AS kicks_30d,
           k.last_kick_at                                 AS last_kick_at
      FROM users u
      JOIN departments d ON d.id = u.department_id
      LEFT JOIN directions dir ON dir.id = u.direction_id
      LEFT JOIN LATERAL (
            SELECT gr.name AS group_name, gr.calculation_model_code
              FROM group_operator_memberships gom
              JOIN groups gr ON gr.id = gom.group_id
             WHERE gom.operator_id = u.id
               AND gom.start_date <= %(day)s::date
               AND (gom.end_date IS NULL OR gom.end_date >= %(day)s::date)
             ORDER BY gom.start_date DESC, gom.id DESC
             LIMIT 1
      ) g ON TRUE
      LEFT JOIN (
            SELECT user_id, COUNT(*) AS kicks, MAX(happened_at) AS last_kick_at
              FROM oktell_guard_phone_kicks
             WHERE happened_at >= %(since)s
               AND department_code = %(department_code)s
             GROUP BY user_id
      ) k ON k.user_id = u.id
     WHERE LOWER(COALESCE(u.role, '')) = ANY(%(roles)s)
       AND LOWER(COALESCE(u.status, '')) <> ALL(%(inactive)s)
       AND """ + _DEPARTMENT_CODE_SQL.format(d='d') + """ = %(department_code)s
     ORDER BY u.name, u.id
"""


def list_phone_employees(cursor, department_code, since, as_of, rule=None):
    """Операторы и стажёры отдела — все группы, а не только те, кого касается
    правило: Основа и человек без группы видны с «не участвует». Как и у СЗоВ,
    прятать того, кого ограничитель не покрывает, значит прятать вопрос.

    participates = правило включено и группа человека в правиле.
    """
    code = str(department_code or '').strip().lower()
    if rule is None:
        rule = get_phone_settings(cursor, code)
    cursor.execute(_PHONE_EMPLOYEES_SQL, {
        'department_code': code,
        'op_department_id': OP_DEPARTMENT_FALLBACK_ID,
        'since': since,
        'day': as_of,
        'roles': list(EMPLOYEE_ROLES),
        'inactive': list(INACTIVE_STATUSES),
    })
    rule_groups = set(rule.get('groups') or ())
    out = []
    for row in fetch_all(cursor):
        group = phone.status_group_for(row.get('role'), row.get('group_model'),
                                       row.get('direction_model')) or ''
        out.append({
            'id': row.get('id'),
            'name': row.get('name'),
            'role': row.get('role'),
            'sip_number': row.get('sip_number') or '',
            'department_name': row.get('department_name'),
            'status_group': group,
            'group_label': phone.STATUS_GROUP_LABELS.get(group, ''),
            'group_name': row.get('group_name') or '',
            'participates': bool(rule.get('enabled')) and group in rule_groups,
            'kicks_30d': int(row.get('kicks_30d') or 0),
            'last_kick_at': row.get('last_kick_at'),
        })
    return out


def phone_report(cursor, date_from, date_to, department_code=phone.PHONE_DEPARTMENT_CODE):
    """Отчёт «за какую дату кого сколько раз выкинуло в Офлайн» по ОП.

    Отдел и группа — снимок в строке выброса, а не текущие: перевод человека
    в другую группу или отдел не переписывает прошлые дни. Если за день
    группа менялась, показывается группа последнего выброса.
    """
    cursor.execute(
        """
        SELECT k.happened_at::date                        AS day,
               k.user_id,
               COALESCE(u.name, '(неизвестный)')          AS name,
               COALESCE(btrim(u.sip_number), '')          AS sip_number,
               (ARRAY_AGG(k.status_group ORDER BY k.happened_at DESC, k.id DESC))[1]
                                                          AS status_group,
               COUNT(*)                                   AS kicks,
               MIN(k.happened_at)                         AS first_at,
               MAX(k.happened_at)                         AS last_at
          FROM oktell_guard_phone_kicks k
          LEFT JOIN users u ON u.id = k.user_id
         WHERE k.happened_at >= %(date_from)s::date
           AND k.happened_at < %(date_to)s::date + 1
           AND k.department_code = %(department_code)s
         GROUP BY k.happened_at::date, k.user_id, u.name, u.sip_number
         ORDER BY day DESC, name, k.user_id
        """,
        {'date_from': date_from, 'date_to': date_to,
         'department_code': str(department_code or '').strip().lower()},
    )
    rows = []
    for row in fetch_all(cursor):
        group = str(row.get('status_group') or '')
        rows.append({
            'day': row.get('day'),
            'user_id': row.get('user_id'),
            'name': row.get('name'),
            'sip_number': row.get('sip_number') or '',
            'status_group': group,
            'group_label': phone.STATUS_GROUP_LABELS.get(group, ''),
            'kicks': int(row.get('kicks') or 0),
            'first_at': row.get('first_at'),
            'last_at': row.get('last_at'),
        })
    return rows


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
               """ + _DEPARTMENT_CODE_SQL.format(d='d') + """ AS department_code,
               EXISTS (
                   SELECT 1 FROM departments h
                    WHERE h.head_user_id = u.id AND h.is_active
               )                     AS is_department_head,
               -- Порядок обязателен: глава двух отделов раньше получал один из
               -- них наугад (LIMIT 1 без ORDER BY), и раздел то открывался, то нет.
               COALESCE((
                   SELECT """ + _DEPARTMENT_CODE_SQL.format(d='h') + """
                     FROM departments h
                    WHERE h.head_user_id = u.id AND h.is_active
                    ORDER BY 1, h.id
                    LIMIT 1
               ), '')                AS headed_department_code,
               ARRAY(
                   SELECT """ + _DEPARTMENT_CODE_SQL.format(d='h') + """
                     FROM departments h
                    WHERE h.head_user_id = u.id AND h.is_active
                    ORDER BY 1, h.id
               )                     AS headed_department_codes
          FROM users u
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE u.id = %(user_id)s
        """,
        {'user_id': int(user_id), 'op_department_id': OP_DEPARTMENT_FALLBACK_ID},
    )
    ctx = fetch_one(cursor)
    if not ctx:
        return None
    headed = [str(code or '').strip().lower()
              for code in (ctx.get('headed_department_codes') or [])]
    headed = [code for code in headed if code]
    if not headed and ctx.get('headed_department_code'):
        headed = [str(ctx['headed_department_code']).strip().lower()]
    ctx['headed_department_codes'] = headed
    # Глава отдела считается по отделу, которым он РУКОВОДИТ: его собственный
    # department_id может быть не заполнен или указывать на другой отдел. Если
    # он возглавляет и свой отдел — оставляем свой: так department_code не
    # зависит от того, какой из двух отделов раньше по алфавиту.
    if ctx.get('is_department_head') and headed:
        own = str(ctx.get('department_code') or '').strip().lower()
        ctx['department_code'] = own if own in headed else headed[0]
    return ctx
