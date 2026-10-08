"""SQL-слой раздела «Списки Байги».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют ни
пулом, ни транзакцией — их держит вызывающий. Загрузка недели меняет четыре
вещи сразу (запись журнала, файл, строки, закрытие прежней загрузки), и только
один курсор гарантирует «все строки или ни одной» (п. 4 постановки).

Контекст доступа — свой: профиль, отделы, где человек глава, и уровни выдач,
под которые он подпадает (кнопка «Доступ»), одним запросом.
"""

from psycopg2 import Binary
from psycopg2.extras import Json, execute_values

from . import schema

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Поля строки для экрана — в порядке выборки. Список ОДИН: и SELECT, и разбор
# собираются из него, колонку нельзя добавить в запрос и забыть в разборе.
# Ключей поиска (search_text, *_key) здесь нет: экрану они не нужны.
ROW_FIELDS = (
    'id', 'period_start', 'zachet', 'sheet_order', 'row_number', 'position', 'date_text',
    'week_number', 'driver_name', 'prize_name', 'prize_amount', 'has_prize', 'amount',
    'trips', 'city', 'park', 'license', 'driver_id',
)
_ROW_COLUMNS = ', '.join('r.%s' % name for name in ROW_FIELDS)

# Колонки записи строк — в порядке значений execute_values.
_INSERT_FIELDS = (
    'upload_id', 'period_start', 'sheet_order', 'row_number', 'zachet', 'position', 'date_text',
    'row_date', 'week_number', 'driver_name', 'prize_name', 'prize_amount', 'has_prize', 'amount',
    'trips', 'city', 'park', 'license', 'license_key', 'driver_id', 'driver_key', 'search_text',
)

UPLOAD_FIELDS = (
    'id', 'campaign', 'period_start', 'period_end', 'period_source', 'week_number', 'file_name',
    'file_size', 'rows_count', 'drivers_count', 'prize_rows', 'prize_total', 'sheets',
    'warnings_count', 'status', 'uploaded_by_name', 'uploaded_at', 'closed_by_name', 'closed_at',
    'replaced_by',
)
_UPLOAD_COLUMNS = ', '.join('u.%s' % name for name in UPLOAD_FIELDS)


def _rows(cursor, fields):
    return [dict(zip(fields, raw)) for raw in cursor.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Контекст доступа
# ─────────────────────────────────────────────────────────────────────────────

# Кто числится в штате: уволенный в портал не входит, а человек в отпуске или
# на больничном — входит, и выданный раздел у него есть. По этому же признаку
# считаются люди под выдачей, чтобы число в листе доступа совпадало с кругом.
_STAFF = "status NOT IN ('fired', 'dismissal')"

# Состав группы — тем же определением, что у вики (wiki/queries.py, my_groups):
# операторы и супервайзеры с действующим членством в действующей группе. Слово
# «группа» в выдаче обязано значить то же, что в остальном портале.
_MEMBERSHIPS = """
    SELECT group_id, operator_id AS user_id, start_date, end_date
      FROM group_operator_memberships
    UNION ALL
    SELECT group_id, supervisor_id, start_date, end_date
      FROM group_supervisor_memberships
"""
_CURRENT = "m.start_date <= CURRENT_DATE AND (m.end_date IS NULL OR m.end_date >= CURRENT_DATE)"

# Запрос собран склейкой, а не подстановкой через %: в нём живут параметры
# psycopg2 (%(user_id)s), и двойная подстановка требовала бы считать проценты.
_CONTEXT_PROFILE = """
WITH me AS (
    SELECT id, name, role, department_id, city FROM users WHERE id = %(user_id)s
),
headed AS (
    SELECT d.id, d.code FROM departments d WHERE d.head_user_id = %(user_id)s AND d.is_active
)"""

# Выдачи, под которые человек подпадает: ему самому, его группе, его отделу.
# Глава отдела входит в отдел и тогда, когда сам числится в другом, — как в
# «Посылках» (parcels.access._belongs_to). Отдел — только действующий: закрытому
# выдача ничего не открывает, как архивной группе, и лист доступа так его и
# подписывает (list_grants: active).
_CONTEXT_GRANTS = """,
my_departments AS (
    SELECT d.id FROM departments d
     WHERE d.is_active AND d.id = (SELECT department_id FROM me)
    UNION
    SELECT id FROM headed
),
my_groups AS (
    SELECT m.group_id
      FROM (""" + _MEMBERSHIPS + """) m
      JOIN groups g ON g.id = m.group_id AND g.status = 'active'
     WHERE m.user_id = %(user_id)s AND """ + _CURRENT + """
),
my_grants AS (
    SELECT a.level
      FROM baiga_access_grants a
     WHERE (a.subject_type = 'user' AND a.subject_id = %(user_id)s)
        OR (a.subject_type = 'group' AND a.subject_id IN (SELECT group_id FROM my_groups))
        OR (a.subject_type = 'department' AND a.subject_id IN (SELECT id FROM my_departments))
)"""

_CONTEXT_COLUMNS = """
SELECT
    (SELECT name          FROM me),
    (SELECT role          FROM me),
    (SELECT department_id FROM me),
    (SELECT d.code FROM departments d WHERE d.id = (SELECT department_id FROM me)),
    (SELECT city          FROM me),
    COALESCE((SELECT array_agg(id)   FROM headed), '{}'),
    COALESCE((SELECT array_agg(code) FROM headed), '{}'),
    """

_GRANT_LEVELS = "COALESCE((SELECT array_agg(DISTINCT level) FROM my_grants), '{}')"
_NO_GRANT_LEVELS = "'{}'::varchar[]"
# Правки круга (лист «Доступ» → «Открыт по умолчанию») — одни на всех: строка
# круга → уровень. Таблица в десяток строк, и читается она тем же запросом.
_CIRCLE_LEVELS = "COALESCE((SELECT json_object_agg(slot, level) FROM baiga_access_circle), '{}'::json)"
_NO_CIRCLE_LEVELS = "'{}'::json"

_CONTEXT_WITH_GRANTS_SQL = (_CONTEXT_PROFILE + _CONTEXT_GRANTS + _CONTEXT_COLUMNS
                            + _GRANT_LEVELS + ',\n    ' + _CIRCLE_LEVELS)
_CONTEXT_WITHOUT_CIRCLE_SQL = (_CONTEXT_PROFILE + _CONTEXT_GRANTS + _CONTEXT_COLUMNS
                               + _GRANT_LEVELS + ',\n    ' + _NO_CIRCLE_LEVELS)
_CONTEXT_WITHOUT_GRANTS_SQL = (_CONTEXT_PROFILE + _CONTEXT_COLUMNS
                               + _NO_GRANT_LEVELS + ',\n    ' + _NO_CIRCLE_LEVELS)


def _context_sql(cursor):
    """Таблиц выдач или правок круга ещё нет (миграция не легла) — контекст
    тот же, без них: раздел живёт по тому, что развёрнуто, а не падает."""
    if not schema.grants_ready(cursor):
        return _CONTEXT_WITHOUT_GRANTS_SQL
    return _CONTEXT_WITH_GRANTS_SQL if schema.circle_ready(cursor) else _CONTEXT_WITHOUT_CIRCLE_SQL


def load_access_context(cursor, user_id):
    """Профиль, главенство, уровни выдач и правки круга — одним запросом.

    Должность отдаётся как в карточке, а не сведённой к оператору: замку нужно
    отличать кадровика от оператора (access.requires_sensitive_qr).
    """
    cursor.execute(_context_sql(cursor), {'user_id': int(user_id)})
    row = cursor.fetchone()
    if not row or row[1] is None:
        return None
    name, role, department_id, department_code, city, headed, headed_codes, grant_levels, circle_levels = row
    return {
        'user_id': int(user_id),
        'name': name,
        'role': str(role or '').strip().lower(),
        'department_id': department_id,
        'department_code': department_code,
        'city': city,
        'headed_department_ids': list(headed or []),
        'headed_department_codes': list(headed_codes or []),
        'grant_levels': list(grant_levels or []),
        'circle_levels': dict(circle_levels or {}),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Экран: недели и варианты фильтров
# ─────────────────────────────────────────────────────────────────────────────

def list_weeks(cursor, campaign):
    """Загруженные недели, свежие сверху — для выбора периода и шапки."""
    cursor.execute(
        "SELECT %s FROM baiga_uploads u WHERE u.campaign = %%(campaign)s AND u.status = 'active' "
        "ORDER BY u.period_start DESC" % _UPLOAD_COLUMNS,
        {'campaign': campaign},
    )
    return _rows(cursor, UPLOAD_FIELDS)


def filter_options(cursor):
    """Значения выпадающих списков по всем неделям сразу.

    Зачёты — в порядке листов файла, а не по алфавиту: «МОТО БАЙГА» в файле
    последний, и в списке ему место там же. Призы — от крупного к мелкому.
    """
    cursor.execute(
        "SELECT zachet FROM baiga_rows GROUP BY zachet ORDER BY MIN(sheet_order), zachet")
    zachets = [row[0] for row in cursor.fetchall()]
    cursor.execute("SELECT DISTINCT city FROM baiga_rows WHERE city <> '' ORDER BY city")
    cities = [row[0] for row in cursor.fetchall()]
    cursor.execute("SELECT DISTINCT park FROM baiga_rows WHERE park <> '' ORDER BY park")
    parks = [row[0] for row in cursor.fetchall()]
    cursor.execute(
        "SELECT prize_name, MAX(prize_amount) FROM baiga_rows WHERE has_prize "
        "GROUP BY prize_name ORDER BY MAX(prize_amount) DESC NULLS LAST, prize_name")
    prizes = [{'name': row[0], 'amount': row[1]} for row in cursor.fetchall()]
    return {'zachets': zachets, 'cities': cities, 'parks': parks, 'prizes': prizes}


# ─────────────────────────────────────────────────────────────────────────────
# Поиск
# ─────────────────────────────────────────────────────────────────────────────

def search(cursor, where_sql, params, order_sql, limit, offset):
    query = "SELECT %s FROM baiga_rows r WHERE %s ORDER BY %s LIMIT %%(limit)s OFFSET %%(offset)s" % (
        _ROW_COLUMNS, where_sql, order_sql)
    cursor.execute(query, dict(params, limit=int(limit), offset=int(offset)))
    return _rows(cursor, ROW_FIELDS)


def totals(cursor, where_sql, params):
    """Итог над таблицей: строк, водителей, строк с призом, сумма призов.

    Отдельным запросом, а не COUNT(*) OVER () в странице: за хвостом выборки
    страница пустая, и оконный счётчик вернул бы ноль вместо настоящего итога.
    """
    cursor.execute(
        "SELECT COUNT(*), COUNT(DISTINCT r.driver_key), COUNT(*) FILTER (WHERE r.has_prize), "
        "COALESCE(SUM(r.prize_amount), 0) FROM baiga_rows r WHERE %s" % where_sql,
        params,
    )
    rows, drivers, prize_rows, prize_total = cursor.fetchone()
    return {'rows': int(rows), 'drivers': int(drivers), 'prize_rows': int(prize_rows),
            'prize_total': int(prize_total)}


def zachet_summary(cursor, where_sql, params):
    """Зачёты выборки — для полосы зачётов над таблицей и заголовков групп.

    Считается по фильтрам БЕЗ выбранного зачёта (условие собирает вызывающий):
    иначе, выбрав «Астану», человек видел бы в полосе одну Астану и не мог бы
    перейти к соседнему зачёту. Порядок — порядок листов файла.
    """
    cursor.execute(
        "SELECT r.zachet, COUNT(*), COUNT(*) FILTER (WHERE r.has_prize), COALESCE(SUM(r.prize_amount), 0) "
        "FROM baiga_rows r WHERE %s GROUP BY r.zachet ORDER BY MIN(r.sheet_order), r.zachet" % where_sql,
        params,
    )
    return [{'name': name, 'rows': int(rows), 'prize_rows': int(prize_rows), 'prize_total': int(prize_total)}
            for name, rows, prize_rows, prize_total in cursor.fetchall()]


def found_keys(cursor, where_sql, params):
    """Ключи водителей выборки — чтобы назвать, кто из вставленного списка не
    нашёлся. Выборка со списком мала (сколько вставили × недели)."""
    cursor.execute("SELECT DISTINCT r.driver_key, r.license_key FROM baiga_rows r WHERE %s" % where_sql,
                   params)
    drivers, licenses = set(), set()
    for driver, license_value in cursor.fetchall():
        drivers.add(driver)
        if license_value:
            licenses.add(license_value)
    return drivers, licenses


def export_rows(cursor, where_sql, params, order_sql, limit):
    """Строки выгрузки, не больше limit (+1 — чтобы заметить превышение)."""
    query = "SELECT %s FROM baiga_rows r WHERE %s ORDER BY %s LIMIT %%(limit)s" % (
        _ROW_COLUMNS, where_sql, order_sql)
    cursor.execute(query, dict(params, limit=int(limit) + 1))
    return _rows(cursor, ROW_FIELDS)


# ─────────────────────────────────────────────────────────────────────────────
# ИИ-помощник (baiga/assistant.py)
# ─────────────────────────────────────────────────────────────────────────────

# Город, парк и зачёт — чтобы слово вопроса «из Алматы», «из парка „Глобал“»
# не считалось посторонним (assistant.identify).
ASSISTANT_CANDIDATE_FIELDS = ('driver_key', 'driver_name', 'license', 'license_key',
                              'city', 'park', 'zachet')
# Поля экрана и ключи, по которым строки собираются в одного человека.
ASSISTANT_ROW_FIELDS = ROW_FIELDS + ('upload_id', 'license_key', 'driver_key')


def _assistant_keys(driver_keys, license_keys):
    """Условие «этот водитель»: по ID и по номеру ВУ. Пустой номер ВУ ничего не
    находит — иначе нашлись бы все строки без номера (та же ловушка, что у
    вставленного списка в filters.where)."""
    clauses, params = [], {}
    if driver_keys:
        clauses.append('r.driver_key = ANY(%(drivers)s)')
        params['drivers'] = list(driver_keys)
    if license_keys:
        clauses.append("(r.license_key <> '' AND r.license_key = ANY(%(licenses)s))")
        params['licenses'] = list(license_keys)
    return clauses, params


def assistant_candidates(cursor, *, driver_keys=(), license_keys=(), name_regex=None, limit=300):
    """Водители, подходящие под ключи вопроса помощнику, — по одному на ID, из
    самой свежей недели, где он есть. Ищет по ВСЕМ неделям: водитель, которого
    нет в спрошенной неделе, всё равно должен узнаваться.

    name_regex — регулярное выражение по search_text (assistant.names_regex):
    «фамилия — одна из названных». Едет значением параметра, как и всё
    остальное: в текст запроса пользовательское слово не попадает.
    """
    clauses, params = _assistant_keys(driver_keys, license_keys)
    if name_regex:
        params['names'] = name_regex
        clauses.append('r.search_text ~ %(names)s')
    if not clauses:
        return []
    cursor.execute(
        "SELECT DISTINCT ON (r.driver_key) %s FROM baiga_rows r WHERE %s "
        "ORDER BY r.driver_key, r.period_start DESC, r.id DESC LIMIT %%(limit)s"
        % (', '.join('r.%s' % name for name in ASSISTANT_CANDIDATE_FIELDS), ' OR '.join(clauses)),
        dict(params, limit=int(limit)),
    )
    return _rows(cursor, ASSISTANT_CANDIDATE_FIELDS)


def assistant_rows(cursor, *, driver_keys=(), license_keys=(), limit=2000):
    """Строки названных водителей по всем неделям, свежие сверху. Водителей —
    единицы (assistant.MAX_PEOPLE), поэтому строк здесь «люди × недели»."""
    clauses, params = _assistant_keys(driver_keys, license_keys)
    if not clauses:
        return []
    cursor.execute(
        "SELECT %s, r.upload_id, r.license_key, r.driver_key FROM baiga_rows r WHERE %s "
        "ORDER BY r.period_start DESC, r.sheet_order, r.position, r.id LIMIT %%(limit)s"
        % (_ROW_COLUMNS, ' OR '.join(clauses)),
        dict(params, limit=int(limit)),
    )
    return _rows(cursor, ASSISTANT_ROW_FIELDS)


def assistant_places(cursor, *, upload_id, first, last, zachets=(), limit=13):
    """Строки недели по месту — «кто занял первое место в Алматы»: места с first по
    last в названных зачётах или, если зачёт не назван, в каждом. Неделя — по id
    загрузки: строки заменённой недели сюда не попадут."""
    params = {'upload': int(upload_id), 'first': int(first), 'last': int(last), 'limit': int(limit)}
    named = ''
    if zachets:
        named = ' AND r.zachet = ANY(%(zachets)s)'
        params['zachets'] = list(zachets)
    cursor.execute(
        "SELECT %s, r.upload_id, r.license_key, r.driver_key FROM baiga_rows r "
        "WHERE r.upload_id = %%(upload)s AND r.position BETWEEN %%(first)s AND %%(last)s%s "
        "ORDER BY r.sheet_order, r.position, r.id LIMIT %%(limit)s" % (_ROW_COLUMNS, named),
        params,
    )
    return _rows(cursor, ASSISTANT_ROW_FIELDS)


# ─────────────────────────────────────────────────────────────────────────────
# Загрузка недели
# ─────────────────────────────────────────────────────────────────────────────

def lock_week(cursor, campaign, period_start):
    """Транзакционная блокировка недели: две одновременные загрузки одной
    недели идут по очереди, и вторая видит, что неделя уже есть."""
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%(key)s))",
                   {'key': 'baiga:%s:%s' % (campaign, period_start.isoformat())})


def find_active_upload(cursor, campaign, period_start):
    cursor.execute(
        "SELECT %s, u.file_sha256 FROM baiga_uploads u WHERE u.campaign = %%(campaign)s "
        "AND u.period_start = %%(period)s AND u.status = 'active'" % _UPLOAD_COLUMNS,
        {'campaign': campaign, 'period': period_start},
    )
    raw = cursor.fetchone()
    if not raw:
        return None
    upload = dict(zip(UPLOAD_FIELDS + ('file_sha256',), raw))
    return upload


def find_overlapping_uploads(cursor, campaign, period_start, period_end):
    """Активные недели, которые ПЕРЕСЕКАЮТСЯ с периодом, но начинаются в другой
    день. Такая загрузка легла бы рядом (ключ недели — дата начала), и каждый
    водитель оказался бы в поиске дважды: это не замена, а дубль."""
    cursor.execute(
        "SELECT %s FROM baiga_uploads u WHERE u.campaign = %%(campaign)s AND u.status = 'active' "
        "AND u.period_start <> %%(start)s AND u.period_start <= %%(end)s AND u.period_end >= %%(start)s "
        "ORDER BY u.period_start" % _UPLOAD_COLUMNS,
        {'campaign': campaign, 'start': period_start, 'end': period_end},
    )
    return _rows(cursor, UPLOAD_FIELDS)


def insert_upload(cursor, result, actor):
    period = result['period']
    stats = result['stats']
    cursor.execute(
        """
        INSERT INTO baiga_uploads (
            campaign, period_start, period_end, period_source, week_number, file_name, file_size,
            file_sha256, rows_count, drivers_count, prize_rows, prize_total, sheets,
            warnings_count, uploaded_by, uploaded_by_name
        ) VALUES (
            %(campaign)s, %(start)s, %(end)s, %(source)s, %(week)s, %(file_name)s, %(file_size)s,
            %(sha)s, %(rows)s, %(drivers)s, %(prize_rows)s, %(prize_total)s, %(sheets)s,
            %(warnings)s, %(actor_id)s, %(actor_name)s
        ) RETURNING id
        """,
        {
            'campaign': result['campaign'], 'start': period['start'], 'end': period['end'],
            'source': period['source'], 'week': period.get('week'),
            'file_name': result['file_name'] or 'файл.xlsx', 'file_size': result['file_size'],
            'sha': result['sha256'], 'rows': stats['rows'], 'drivers': stats['drivers'],
            'prize_rows': stats['prize_rows'], 'prize_total': stats['prize_total'],
            'sheets': Json([{'name': sheet['name'], 'rows': sheet['rows']} for sheet in result['sheets']]),
            'warnings': result['warnings_total'],
            'actor_id': actor['user_id'], 'actor_name': actor.get('name'),
        },
    )
    return cursor.fetchone()[0]


def insert_file(cursor, upload_id, content):
    cursor.execute("INSERT INTO baiga_upload_files (upload_id, content) VALUES (%(id)s, %(content)s)",
                   {'id': upload_id, 'content': Binary(content)})


def insert_rows(cursor, upload_id, period_start, rows):
    """Строки недели пачками. Сколько записано — проверяется отдельным COUNT:
    rowcount у execute_values считает только последнюю пачку."""
    values = []
    for row in rows:
        source = dict(row, upload_id=upload_id, period_start=period_start)
        values.append(tuple(source[name] for name in _INSERT_FIELDS))
    execute_values(
        cursor,
        "INSERT INTO baiga_rows (%s) VALUES %%s" % ', '.join(_INSERT_FIELDS),
        values,
        page_size=1000,
    )
    cursor.execute("SELECT COUNT(*) FROM baiga_rows WHERE upload_id = %(id)s", {'id': upload_id})
    return cursor.fetchone()[0]


def close_upload(cursor, upload_id, status, actor, replaced_by=None):
    """Закрыть загрузку: статус в журнал, строки и файл — прочь."""
    cursor.execute(
        "UPDATE baiga_uploads SET status = %%(status)s, closed_by = %%(actor_id)s, "
        "closed_by_name = %%(actor_name)s, closed_at = %s, replaced_by = %%(replaced_by)s "
        "WHERE id = %%(id)s" % _NOW,
        {'status': status, 'actor_id': actor['user_id'], 'actor_name': actor.get('name'),
         'replaced_by': replaced_by, 'id': upload_id},
    )
    cursor.execute("DELETE FROM baiga_rows WHERE upload_id = %(id)s", {'id': upload_id})
    cursor.execute("DELETE FROM baiga_upload_files WHERE upload_id = %(id)s", {'id': upload_id})


def mark_replaced_by(cursor, upload_id, replaced_by):
    """Ссылка «заменена загрузкой N» — ставится после записи новой загрузки:
    прежнюю приходится закрыть ДО неё, иначе две активные загрузки одной недели
    не пустит уникальный индекс."""
    cursor.execute("UPDATE baiga_uploads SET replaced_by = %(new)s WHERE id = %(id)s",
                   {'new': replaced_by, 'id': upload_id})


def get_upload(cursor, upload_id, lock=False):
    cursor.execute(
        "SELECT %s FROM baiga_uploads u WHERE u.id = %%(id)s%s" % (_UPLOAD_COLUMNS, ' FOR UPDATE' if lock else ''),
        {'id': int(upload_id)},
    )
    raw = cursor.fetchone()
    return dict(zip(UPLOAD_FIELDS, raw)) if raw else None


def read_file(cursor, upload_id):
    """Байты исходника или None — у заменённой и удалённой недели файла нет."""
    cursor.execute("SELECT content FROM baiga_upload_files WHERE upload_id = %(id)s", {'id': int(upload_id)})
    raw = cursor.fetchone()
    return bytes(raw[0]) if raw else None


# ─────────────────────────────────────────────────────────────────────────────
# Журнал
# ─────────────────────────────────────────────────────────────────────────────

def list_uploads(cursor, limit=200):
    cursor.execute(
        "SELECT %s, EXISTS (SELECT 1 FROM baiga_upload_files f WHERE f.upload_id = u.id) "
        "FROM baiga_uploads u ORDER BY u.uploaded_at DESC, u.id DESC LIMIT %%(limit)s" % _UPLOAD_COLUMNS,
        {'limit': int(limit)},
    )
    return _rows(cursor, UPLOAD_FIELDS + ('has_file',))


EXPORT_FIELDS = ('id', 'kind', 'mode', 'filters', 'rows_count', 'upload_id', 'actor_name', 'created_at',
                 'upload_period_start', 'upload_period_end', 'upload_file_name')


def list_exports(cursor, limit=200):
    cursor.execute(
        "SELECT e.id, e.kind, e.mode, e.filters, e.rows_count, e.upload_id, e.actor_name, e.created_at, "
        "u.period_start, u.period_end, u.file_name "
        "FROM baiga_exports e LEFT JOIN baiga_uploads u ON u.id = e.upload_id "
        "ORDER BY e.created_at DESC, e.id DESC LIMIT %(limit)s",
        {'limit': int(limit)},
    )
    return _rows(cursor, EXPORT_FIELDS)


def log_export(cursor, *, kind, actor, rows_count=0, mode=None, filters=None, upload_id=None):
    cursor.execute(
        "INSERT INTO baiga_exports (kind, mode, filters, rows_count, upload_id, actor_user_id, actor_name) "
        "VALUES (%(kind)s, %(mode)s, %(filters)s, %(rows)s, %(upload_id)s, %(actor_id)s, %(actor_name)s)",
        {'kind': kind, 'mode': mode, 'filters': Json(filters or {}), 'rows': int(rows_count or 0),
         'upload_id': upload_id, 'actor_id': actor['user_id'], 'actor_name': actor.get('name')},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Доступ: выдачи из раздела (кнопка «Доступ»)
# ─────────────────────────────────────────────────────────────────────────────

# Люди в штате и число людей в каждой группе — общая шапка списка выдач и
# справочника адресатов: число в строке отвечает на вопрос «кому я открываю».
_PEOPLE_CTES = """
WITH staff AS (
    SELECT id, name, role, department_id FROM users WHERE """ + _STAFF + """
),
group_people AS (
    SELECT m.group_id, COUNT(DISTINCT m.user_id) AS people
      FROM (""" + _MEMBERSHIPS + """) m
      JOIN staff s ON s.id = m.user_id
     WHERE """ + _CURRENT + """
     GROUP BY 1
)"""

GRANT_FIELDS = ('id', 'subject_type', 'subject_id', 'level', 'granted_by_name', 'granted_at',
                'label', 'detail', 'role', 'people', 'active')


def list_grants(cursor):
    """Выдачи с подписями адресатов: отделы, группы, люди — в этом порядке.

    label — как адресат зовётся сейчас (None — его больше нет); detail — отдел
    группы или человека: одноимённые группы разных отделов и тёзок иначе не
    различить; people — сколько человек под выдачей; active — жив ли адресат:
    уволенному, архивной группе и закрытому отделу выдача ничего не открывает.
    """
    cursor.execute(_PEOPLE_CTES + """
        SELECT a.id, a.subject_type, a.subject_id, a.level, a.granted_by_name, a.granted_at,
               COALESCE(u.name, g.name, d.name),
               COALESCE(ud.name, gd.name),
               u.role,
               CASE a.subject_type
                    WHEN 'group' THEN COALESCE(gp.people, 0)
                    WHEN 'department' THEN (SELECT COUNT(*) FROM staff s WHERE s.department_id = d.id)
               END,
               CASE a.subject_type
                    WHEN 'user' THEN COALESCE(u.""" + _STAFF + """, FALSE)
                    WHEN 'group' THEN COALESCE(g.status = 'active', FALSE)
                    ELSE COALESCE(d.is_active, FALSE)
               END
          FROM baiga_access_grants a
          LEFT JOIN users u ON a.subject_type = 'user' AND u.id = a.subject_id
          LEFT JOIN departments ud ON ud.id = u.department_id
          LEFT JOIN groups g ON a.subject_type = 'group' AND g.id = a.subject_id
          LEFT JOIN departments gd ON gd.id = g.department_id
          LEFT JOIN group_people gp ON gp.group_id = g.id
          LEFT JOIN departments d ON a.subject_type = 'department' AND d.id = a.subject_id
         ORDER BY CASE a.subject_type WHEN 'department' THEN 1 WHEN 'group' THEN 2 ELSE 3 END,
                  7 NULLS LAST, a.id
    """)
    return _rows(cursor, GRANT_FIELDS)


def access_catalog(cursor):
    """Кому можно выдать: действующие отделы и группы, люди в штате — одним
    запросом. {вид: [{id, name, detail, role, people}]}."""
    cursor.execute(_PEOPLE_CTES + """
        SELECT 'department', d.id, d.name::text, NULL::text, NULL::text,
               (SELECT COUNT(*) FROM staff s WHERE s.department_id = d.id)
          FROM departments d
         WHERE d.is_active
        UNION ALL
        SELECT 'group', g.id, g.name::text, gd.name::text, NULL::text, COALESCE(gp.people, 0)
          FROM groups g
          LEFT JOIN departments gd ON gd.id = g.department_id
          LEFT JOIN group_people gp ON gp.group_id = g.id
         WHERE g.status = 'active'
        UNION ALL
        SELECT 'user', s.id, s.name::text, sd.name::text, s.role::text, NULL::bigint
          FROM staff s
          LEFT JOIN departments sd ON sd.id = s.department_id
         ORDER BY 1, 3, 2
    """)
    catalog = {'department': [], 'group': [], 'user': []}
    for kind, ident, name, detail, role, people in cursor.fetchall():
        catalog[kind].append({'id': ident, 'name': name, 'detail': detail, 'role': role,
                              'people': None if people is None else int(people)})
    return catalog


def circle_names(cursor, department_codes, user_ids):
    """Названия отделов круга и имена названных поимённо: ({код: название},
    {id: имя}). Коды в базе встречаются в разном регистре («SZOV») — сверяем
    без него, как сам круг."""
    cursor.execute("""
        SELECT 'department', lower(trim(d.code)), d.name FROM departments d
         WHERE d.is_active AND lower(trim(d.code)) = ANY(%(codes)s::text[])
        UNION ALL
        SELECT 'user', u.id::text, u.name FROM users u
         WHERE u.id = ANY(%(users)s::int[]) AND u.""" + _STAFF + """
    """, {'codes': list(department_codes), 'users': list(user_ids)})
    departments, people = {}, {}
    for kind, key, name in cursor.fetchall():
        if kind == 'department':
            departments[key] = name
        else:
            people[int(key)] = name
    return departments, people


def find_subjects(cursor, subjects):
    """Подписи живых адресатов из списка (вид, id): {(вид, id): подпись}.

    Кого в ответе нет — уволен, в архиве, закрыт или не существует вовсе:
    такому не выдают. Проверка одна на всю пачку, а не запрос на адресата.
    """
    ids = {'user': [], 'group': [], 'department': []}
    for kind, ident in subjects:
        ids[kind].append(int(ident))
    cursor.execute("""
        SELECT 'user', u.id, u.name FROM users u
         WHERE u.id = ANY(%(user)s::int[]) AND u.""" + _STAFF + """
        UNION ALL
        SELECT 'group', g.id, g.name FROM groups g
         WHERE g.id = ANY(%(group)s::int[]) AND g.status = 'active'
        UNION ALL
        SELECT 'department', d.id, d.name FROM departments d
         WHERE d.id = ANY(%(department)s::int[]) AND d.is_active
    """, ids)
    return {(kind, int(ident)): name for kind, ident, name in cursor.fetchall()}


def lock_access(cursor):
    """Правки доступа идут по очереди: «было → стало» в журнале и счёт
    «выдано N» считаются по прочитанному, и два раздающих не должны читать
    одно и то же состояние."""
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext('baiga:access'))")


def granted_levels(cursor, subjects):
    """Что адресатам уже выдано: {(вид, id): уровень}."""
    pairs = tuple((kind, int(ident)) for kind, ident in subjects)
    if not pairs:
        return {}
    cursor.execute(
        "SELECT subject_type, subject_id, level FROM baiga_access_grants "
        "WHERE (subject_type, subject_id) IN %(pairs)s", {'pairs': pairs})
    return {(kind, int(ident)): level for kind, ident, level in cursor.fetchall()}


def write_grants(cursor, subjects, level, actor):
    """Выдать уровень адресатам [(вид, id, подпись)] одной пачкой: нового —
    добавить, у записанного — сменить уровень. Возвращает (выдано, изменено).

    Адресат, у которого этот уровень уже стоит, не трогается и в журнал не
    идёт: «выдал» у его выдачи остался бы чужим именем без единой правки.
    """
    before = granted_levels(cursor, [(kind, ident) for kind, ident, _ in subjects])
    fresh = [item for item in subjects if (item[0], item[1]) not in before]
    changed = [item for item in subjects
               if (item[0], item[1]) in before and before[(item[0], item[1])] != level]
    if fresh or changed:
        execute_values(
            cursor,
            "INSERT INTO baiga_access_grants (subject_type, subject_id, level, granted_by, granted_by_name) "
            "VALUES %s ON CONFLICT (subject_type, subject_id) DO UPDATE SET level = EXCLUDED.level, "
            "granted_by = EXCLUDED.granted_by, granted_by_name = EXCLUDED.granted_by_name, "
            "granted_at = " + _NOW,
            [(kind, ident, level, actor['user_id'], actor.get('name')) for kind, ident, _ in fresh + changed],
        )
        log_access(cursor, actor, [
            {'action': 'grant', 'subject_type': kind, 'subject_id': ident, 'label': label, 'after': level}
            for kind, ident, label in fresh
        ] + [
            {'action': 'change', 'subject_type': kind, 'subject_id': ident, 'label': label,
             'before': before[(kind, ident)], 'after': level}
            for kind, ident, label in changed
        ])
    return len(fresh), len(changed)


GRANT_ROW_FIELDS = ('id', 'subject_type', 'subject_id', 'level', 'label')


def get_grant(cursor, grant_id, lock=False):
    """Одна выдача с подписью адресата (для журнала) или None."""
    cursor.execute(
        "SELECT a.id, a.subject_type, a.subject_id, a.level, COALESCE(u.name, g.name, d.name) "
        "FROM baiga_access_grants a "
        "LEFT JOIN users u ON a.subject_type = 'user' AND u.id = a.subject_id "
        "LEFT JOIN groups g ON a.subject_type = 'group' AND g.id = a.subject_id "
        "LEFT JOIN departments d ON a.subject_type = 'department' AND d.id = a.subject_id "
        "WHERE a.id = %%(id)s%s" % (' FOR UPDATE OF a' if lock else ''),
        {'id': int(grant_id)},
    )
    raw = cursor.fetchone()
    return dict(zip(GRANT_ROW_FIELDS, raw)) if raw else None


def set_grant_level(cursor, grant, level, actor):
    cursor.execute(
        "UPDATE baiga_access_grants SET level = %%(level)s, granted_by = %%(actor_id)s, "
        "granted_by_name = %%(actor_name)s, granted_at = %s WHERE id = %%(id)s" % _NOW,
        {'level': level, 'actor_id': actor['user_id'], 'actor_name': actor.get('name'), 'id': grant['id']},
    )
    log_access(cursor, actor, [{'action': 'change', 'subject_type': grant['subject_type'],
                                'subject_id': grant['subject_id'], 'label': grant.get('label'),
                                'before': grant['level'], 'after': level}])


def delete_grant(cursor, grant, actor):
    cursor.execute("DELETE FROM baiga_access_grants WHERE id = %(id)s", {'id': grant['id']})
    log_access(cursor, actor, [{'action': 'revoke', 'subject_type': grant['subject_type'],
                                'subject_id': grant['subject_id'], 'label': grant.get('label'),
                                'before': grant['level']}])


def circle_edits(cursor):
    """Правки круга: {строка: {уровень, кто и когда правил}}. Свежим чтением —
    под блокировкой правок доступа «было → стало» в журнале не расходится с
    таблицей; лист показывает по ним, кто сменил уровень строки."""
    cursor.execute("SELECT slot, level, updated_by_name, updated_at FROM baiga_access_circle")
    return {slot: {'level': level, 'updated_by_name': name, 'updated_at': stamp}
            for slot, level, name, stamp in cursor.fetchall()}


def set_circle_level(cursor, slot, before, level, actor):
    """Записать уровень строки круга и оставить след: before — уровень, который
    действовал до правки (по умолчанию или прежняя правка)."""
    cursor.execute(
        "INSERT INTO baiga_access_circle (slot, level, updated_by, updated_by_name) "
        "VALUES (%%(slot)s, %%(level)s, %%(actor_id)s, %%(actor_name)s) "
        "ON CONFLICT (slot) DO UPDATE SET level = EXCLUDED.level, updated_by = EXCLUDED.updated_by, "
        "updated_by_name = EXCLUDED.updated_by_name, updated_at = %s" % _NOW,
        {'slot': slot, 'level': level, 'actor_id': actor['user_id'], 'actor_name': actor.get('name')},
    )
    # Адресат строки круга — не человек, не группа и не отдел: в журнале он
    # записан именем строки, номера у него нет.
    log_access(cursor, actor, [{'action': 'circle', 'subject_type': 'circle', 'subject_id': 0,
                                'label': slot, 'before': before, 'after': level}])


def log_access(cursor, actor, entries):
    """След правок доступа: кто, кому, что было и что стало — пачкой."""
    if not entries:
        return
    execute_values(
        cursor,
        "INSERT INTO baiga_access_log (action, subject_type, subject_id, subject_label, level_before, "
        "level_after, actor_user_id, actor_name) VALUES %s",
        [(entry['action'], entry['subject_type'], entry['subject_id'], entry.get('label'),
          entry.get('before'), entry.get('after'), actor['user_id'], actor.get('name')) for entry in entries],
    )
