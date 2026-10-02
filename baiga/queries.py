"""SQL-слой раздела «Списки Байги».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют ни
пулом, ни транзакцией — их держит вызывающий. Загрузка недели меняет четыре
вещи сразу (запись журнала, файл, строки, закрытие прежней загрузки), и только
один курсор гарантирует «все строки или ни одной» (п. 4 постановки).

Контекст доступа — общий с «Посылками»: профиль, отдел и отделы, где человек
глава, одним запросом.
"""

from psycopg2 import Binary
from psycopg2.extras import Json, execute_values

from parcels import queries as parcels_queries

load_access_context = parcels_queries.load_access_context

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
