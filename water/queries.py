"""SQL-слой раздела «Учёт воды».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют ни
пулом, ни транзакцией — их держит вызывающий. Выдача меняет две вещи сразу —
остаток офиса и журнал, — и только один курсор гарантирует, что остаток
уменьшился ровно на записанное в журнал.

Контекст доступа и справочник офисов — общие с «Посылками»: периметр отделов у
разделов один (фронт-офисы и СЗоВ), и второй копии тех же запросов быть не
должно.
"""

import json
import math
from datetime import date, datetime, timedelta

from parcels import queries as parcels_queries

from . import access, rules

load_access_context = parcels_queries.load_access_context
now_almaty = parcels_queries.now_almaty
today_almaty = parcels_queries.today_almaty


def _iso(value):
    return value.isoformat() if isinstance(value, (datetime, date)) else value


# ─────────────────────────────────────────────────────────────────────────────
# Настройки
# ─────────────────────────────────────────────────────────────────────────────

SETTINGS_FIELDS = ('min_trips', 'cooldown_days', 'welcome_blocks', 'activity_blocks',
                   'tariffs', 'low_threshold', 'buy_threshold', 'notify_user_ids')

_SETTINGS_DEFAULTS = {
    'min_trips': 20, 'cooldown_days': 7, 'welcome_blocks': 1, 'activity_blocks': 1,
    'tariffs': ['business', 'ultimate'], 'low_threshold': 20, 'buy_threshold': 10,
    'notify_user_ids': [],
}


def _json_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def get_settings(cursor):
    """Условия программы. Строки нет (схема разворачивается) — значения ТЗ."""
    cursor.execute(
        "SELECT min_trips, cooldown_days, welcome_blocks, activity_blocks, tariffs, "
        "low_threshold, buy_threshold, notify_user_ids, updated_by_name, updated_at "
        "FROM water_settings WHERE id = 1")
    row = cursor.fetchone()
    if not row:
        return dict(_SETTINGS_DEFAULTS, updated_by_name=None, updated_at=None)
    settings = dict(zip(SETTINGS_FIELDS, row[:8]))
    settings['tariffs'] = [str(code) for code in _json_list(settings['tariffs'])]
    settings['notify_user_ids'] = [int(x) for x in _json_list(settings['notify_user_ids'])
                                   if str(x).lstrip('-').isdigit()]
    settings['updated_by_name'] = row[8]
    settings['updated_at'] = _iso(row[9])
    return settings


def update_settings(cursor, fields, actor):
    assignments, params = [], []
    for name in SETTINGS_FIELDS:
        if name not in fields:
            continue
        value = fields[name]
        if name in ('tariffs', 'notify_user_ids'):
            assignments.append('%s = %%s::jsonb' % name)
            params.append(json.dumps(value))
        else:
            assignments.append('%s = %%s' % name)
            params.append(value)
    if not assignments:
        return get_settings(cursor)
    assignments += ['updated_by = %s', 'updated_by_name = %s',
                    "updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"]
    params += [actor['user_id'], actor.get('name')]
    cursor.execute('UPDATE water_settings SET %s WHERE id = 1' % ', '.join(assignments), params)
    return get_settings(cursor)


# ─────────────────────────────────────────────────────────────────────────────
# Офисы учёта
# ─────────────────────────────────────────────────────────────────────────────

_OFFICE_COLUMNS = ('id', 'office_id', 'city', 'name', 'address', 'stock', 'low_threshold',
                   'buy_threshold', 'is_active', 'created_at', 'updated_at')


def _office_row(row, settings):
    office = dict(zip(_OFFICE_COLUMNS, row))
    office['own_low_threshold'] = office['low_threshold']
    office['own_buy_threshold'] = office['buy_threshold']
    low = office['low_threshold'] if office['low_threshold'] is not None else settings['low_threshold']
    buy = office['buy_threshold'] if office['buy_threshold'] is not None else settings['buy_threshold']
    office['low_threshold'] = low
    office['buy_threshold'] = buy
    office['status'] = rules.stock_status(office['stock'], low, buy)
    office['created_at'] = _iso(office['created_at'])
    office['updated_at'] = _iso(office['updated_at'])
    return office


def list_offices(cursor, settings, *, include_inactive=False):
    cursor.execute(
        'SELECT %s FROM water_offices %s ORDER BY city, name, id' % (
            ', '.join(_OFFICE_COLUMNS), '' if include_inactive else 'WHERE is_active'))
    return [_office_row(row, settings) for row in cursor.fetchall()]


def read_office(cursor, water_office_id, settings, *, for_update=False):
    """Офис учёта. for_update запирает строку до конца транзакции: остаток
    читается и меняется в одном шаге, и параллельная выдача ждёт своей очереди."""
    cursor.execute(
        'SELECT %s FROM water_offices WHERE id = %%s%s' % (
            ', '.join(_OFFICE_COLUMNS), ' FOR UPDATE' if for_update else ''),
        (int(water_office_id),))
    row = cursor.fetchone()
    return _office_row(row, settings) if row else None


def directory(cursor):
    """Офисы фронт-офисов из справочника вики — те же, что у «Посылок»."""
    return parcels_queries.list_offices(
        cursor, space_ids=parcels_queries.section_space_ids(cursor))


def directory_office(cursor, office_id):
    return parcels_queries.read_office(
        cursor, office_id, space_ids=parcels_queries.section_space_ids(cursor))


def taken_office_ids(cursor):
    cursor.execute('SELECT office_id FROM water_offices WHERE office_id IS NOT NULL')
    return {row[0] for row in cursor.fetchall()}


def add_office(cursor, *, wiki_office, stock, low_threshold, buy_threshold, settings, actor):
    """Завести офис в учёт со стартовым остатком.

    Стартовый остаток — это пересчёт с нуля: в журнале движений он виден
    первой строкой («было 0, насчитали 40»), и сумма журнала сходится с
    остатком с первого дня.
    """
    cursor.execute(
        """
        INSERT INTO water_offices (office_id, city, name, address, stock, low_threshold,
                                   buy_threshold, created_by, created_by_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (wiki_office['id'], wiki_office['city'], wiki_office['name'], wiki_office.get('address'),
         int(stock), low_threshold, buy_threshold, actor['user_id'], actor.get('name')))
    water_office_id = cursor.fetchone()[0]
    _insert_movement(cursor, water_office_id, 'recount', int(stock), int(stock),
                     'Стартовый остаток', actor)
    return read_office(cursor, water_office_id, settings)


def update_office(cursor, water_office_id, fields, settings):
    assignments, params = [], []
    for name in ('low_threshold', 'buy_threshold', 'is_active'):
        if name in fields:
            assignments.append('%s = %%s' % name)
            params.append(fields[name])
    if assignments:
        assignments.append("updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')")
        params.append(int(water_office_id))
        cursor.execute('UPDATE water_offices SET %s WHERE id = %%s' % ', '.join(assignments), params)
    return read_office(cursor, water_office_id, settings)


_MOVEMENT_COLUMNS = ('id', 'kind', 'delta', 'stock_after', 'comment', 'actor_name', 'created_at',
                     'adjusts_intake')


def _movement_row(row):
    movement = dict(zip(_MOVEMENT_COLUMNS, row))
    movement['created_at'] = _iso(movement['created_at'])
    movement['adjusts_intake'] = bool(movement['adjusts_intake'])
    return movement


def _insert_movement(cursor, water_office_id, kind, delta, stock_after, comment, actor,
                     adjusts_intake=False):
    # Отметка «учесть в „Поступило“» есть только у пересчёта: поступление в
    # этой колонке и так.
    adjusts_intake = bool(adjusts_intake) and kind == 'recount'
    cursor.execute(
        """
        INSERT INTO water_movements (water_office_id, kind, delta, stock_after, comment,
                                     actor_user_id, actor_name, adjusts_intake)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, created_at
        """,
        (int(water_office_id), kind, int(delta), int(stock_after), comment,
         actor['user_id'], actor.get('name'), adjusts_intake))
    movement_id, created_at = cursor.fetchone()
    return {'id': movement_id, 'kind': kind, 'delta': int(delta), 'stock_after': int(stock_after),
            'comment': comment, 'actor_name': actor.get('name'), 'created_at': _iso(created_at),
            'adjusts_intake': adjusts_intake}


def apply_movement(cursor, office, *, kind, delta, comment, actor, adjusts_intake=False):
    """Поступление или пересчёт по ЗАПЕРТОЙ строке офиса (read_office for_update).

    Возвращает строку журнала. Остаток ниже нуля не уводится — это проверяет
    вызывающий, а CHECK в таблице страхует.

    adjusts_intake — пересчётом исправляют поступление, и его разница входит в
    «Поступило» остатков (см. dashboard).
    """
    stock_after = int(office['stock']) + int(delta)
    cursor.execute(
        "UPDATE water_offices SET stock = %s, "
        "updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') WHERE id = %s",
        (stock_after, office['id']))
    return _insert_movement(cursor, office['id'], kind, delta, stock_after, comment, actor,
                            adjusts_intake=adjusts_intake)


def list_movements(cursor, water_office_id, limit=30):
    cursor.execute(
        """
        SELECT %s
          FROM water_movements
         WHERE water_office_id = %%s
         ORDER BY created_at DESC, id DESC
         LIMIT %%s
        """ % ', '.join(_MOVEMENT_COLUMNS),
        (int(water_office_id), int(limit)))
    return [_movement_row(row) for row in cursor.fetchall()]


def read_movement(cursor, movement_id):
    """Строка журнала движений — или None, если такой нет."""
    cursor.execute(
        'SELECT %s FROM water_movements WHERE id = %%s' % ', '.join(_MOVEMENT_COLUMNS),
        (int(movement_id),))
    row = cursor.fetchone()
    return _movement_row(row) if row else None


def set_movement_adjusts_intake(cursor, movement_id, value, actor):
    """Сменить у проведённого пересчёта отметку «учесть в „Поступило“».

    Остаток не трогается: меняется только то, входит ли разница пересчёта в
    «Поступило». Поступление отметить нельзя — условие в самом запросе, а не
    только у вызывающего.
    """
    cursor.execute(
        """
        UPDATE water_movements
           SET adjusts_intake = %%s, adjusts_intake_by_name = %%s,
               adjusts_intake_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
         WHERE id = %%s AND kind = 'recount'
        RETURNING %s
        """ % ', '.join(_MOVEMENT_COLUMNS),
        (bool(value), actor.get('name'), int(movement_id)))
    row = cursor.fetchone()
    return _movement_row(row) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Выдачи
# ─────────────────────────────────────────────────────────────────────────────

def lock_driver(cursor, account_id, iin=None):
    """Запирает водителя до конца транзакции — по аккаунту и по ИИН.

    Два офиса, выдающие воду одному человеку в одну секунду, иначе оба
    прочитали бы «прошлой выдачи нет» и оба выдали бы. Ключи берём в одном
    порядке, чтобы две транзакции не заперли их крест-накрест.
    """
    keys = sorted({'water:account:%s' % account_id} | ({'water:iin:%s' % iin} if iin else set()))
    for key in keys:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (key,))


_HISTORY_COLUMNS = ('id', 'kind', 'blocks', 'created_at', 'driver_account_id', 'orders_total',
                    'office_name', 'city', 'issued_by_name')


def person_history(cursor, account_id, iin=None, limit=20):
    """Прошлые выдачи человека — по аккаунту ИЛИ ИИН, новые первыми.

    ИИН нужен потому, что у водителя в каждом парке свой аккаунт: без него
    «приветственный один раз» обходился бы переходом в соседний парк.

    Отменённые выдачи сюда не попадают: отмена для того и существует, чтобы
    ошибочная выдача не включала водителю неделю ожидания и не закрывала ему
    приветственный блок.
    """
    cursor.execute(
        """
        SELECT %s FROM water_issues
         WHERE (driver_account_id = %%(account)s
                OR (%%(iin)s <> '' AND driver_iin = %%(iin)s))
           AND canceled_at IS NULL
         ORDER BY created_at DESC, id DESC
         LIMIT %%(limit)s
        """ % ', '.join(_HISTORY_COLUMNS),
        {'account': str(account_id or ''), 'iin': str(iin or ''), 'limit': int(limit)})
    rows = [dict(zip(_HISTORY_COLUMNS, row)) for row in cursor.fetchall()]
    # Приветственный ищется БЕЗ окна последних выдач. Иначе через полгода
    # еженедельных блоков он уходил за LIMIT, и новый аккаунт того же человека
    # в другом парке снова получал «можно выдать приветственный» — экран
    # обещал то, что потом отклоняла только уникальный индекс базы.
    if not any(row['kind'] == 'welcome' for row in rows):
        cursor.execute(
            """
            SELECT %s FROM water_issues
             WHERE kind = 'welcome'
               AND canceled_at IS NULL
               AND (driver_account_id = %%(account)s
                    OR (%%(iin)s <> '' AND driver_iin = %%(iin)s))
             ORDER BY created_at, id
             LIMIT 1
            """ % ', '.join(_HISTORY_COLUMNS),
            {'account': str(account_id or ''), 'iin': str(iin or '')})
        welcome = cursor.fetchone()
        if welcome:
            # Он старше всего окна (иначе попал бы в него) — значит, в конец.
            rows.append(dict(zip(_HISTORY_COLUMNS, welcome)))
    return rows


def create_issue(cursor, office, driver, verdict, *, blocks, actor):
    """Выдача по ЗАПЕРТЫМ офису и водителю. Остаток уменьшается тем же курсором."""
    stock_after = int(office['stock']) - int(blocks)
    cursor.execute(
        "UPDATE water_offices SET stock = %s, "
        "updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') WHERE id = %s",
        (stock_after, office['id']))
    orders = driver.get('orders') or {}
    cursor.execute(
        """
        INSERT INTO water_issues (
            water_office_id, city, office_name, kind, blocks, stock_after,
            driver_account_id, driver_iin, driver_name, driver_phone, driver_park,
            driver_park_id, driver_tariffs, orders_total, orders_counted, orders_basis,
            fk_source, driver_info, issued_by, issued_by_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s,
                %s, %s::jsonb, %s, %s)
        RETURNING id
        """,
        (office['id'], office['city'], office['name'], verdict['kind'], int(blocks), stock_after,
         driver['account_id'], driver.get('iin'), driver.get('name'), driver.get('phone'),
         driver.get('park'), driver.get('park_id'), json.dumps(driver.get('tariffs') or []),
         orders.get('total'), verdict.get('trips'), verdict.get('trips_basis'),
         verdict.get('fk') if verdict.get('kind') == 'welcome' else None,
         json.dumps(driver.get('info') or {}, ensure_ascii=False, default=str),
         actor['user_id'], actor.get('name')))
    return read_issue(cursor, cursor.fetchone()[0])


_ISSUE_COLUMNS = ('id', 'water_office_id', 'city', 'office_name', 'kind', 'blocks',
                  'stock_after', 'driver_account_id', 'driver_name', 'driver_phone',
                  'driver_park', 'driver_park_id', 'driver_tariffs', 'orders_counted',
                  'orders_basis', 'issued_by', 'issued_by_name', 'created_at',
                  'canceled_at', 'canceled_by_name', 'cancel_reason')


def _issue_row(row):
    item = dict(zip(_ISSUE_COLUMNS, row))
    item['driver_tariffs'] = [str(code) for code in _json_list(item['driver_tariffs'])]
    item['created_at'] = _iso(item['created_at'])
    item['canceled_at'] = _iso(item['canceled_at'])
    return item


def read_issue(cursor, issue_id):
    cursor.execute('SELECT %s FROM water_issues WHERE id = %%s' % ', '.join(_ISSUE_COLUMNS),
                   (int(issue_id),))
    row = cursor.fetchone()
    return _issue_row(row) if row else None


def issue_for_cancel(cursor, issue_id, *, for_update=False):
    """Где, кому и сколько выдано и не отменена ли уже — всё, что нужно отмене.

    ИИН здесь только для замка водителя (lock_driver), наружу он не отдаётся.
    for_update запирает строку: две отмены одной выдачи не вернут блоки дважды.
    """
    cursor.execute(
        'SELECT id, water_office_id, driver_account_id, driver_iin, blocks, canceled_at '
        'FROM water_issues WHERE id = %s' + (' FOR UPDATE' if for_update else ''),
        (int(issue_id),))
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': row[0], 'water_office_id': row[1], 'driver_account_id': row[2],
            'driver_iin': row[3], 'blocks': int(row[4]), 'canceled_at': row[5]}


def cancel_issue(cursor, issue, *, reason, actor):
    """Отмена по ЗАПЕРТЫМ офису, водителю и строке выдачи.

    Строка остаётся в журнале с пометкой «кто, когда, почему», блоки
    возвращаются на остаток тем же курсором: вода не ушла, либо её выдадут
    заново — уже нужному водителю, и это будет новая выдача со своим списанием.
    """
    cursor.execute(
        "UPDATE water_issues SET canceled_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'), "
        "canceled_by = %s, canceled_by_name = %s, cancel_reason = %s "
        "WHERE id = %s AND canceled_at IS NULL",
        (actor['user_id'], actor.get('name'), reason, issue['id']))
    cursor.execute(
        "UPDATE water_offices SET stock = stock + %s, "
        "updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') WHERE id = %s",
        (int(issue['blocks']), int(issue['water_office_id'])))
    return read_issue(cursor, issue['id'])


def _issue_filter(filters, params):
    """WHERE журнала — одно место на список, счётчик и выгрузку.

    Даты — границы по календарю Алматы включительно: created_at хранится
    временем Алматы, поэтому «по 30.09» — это «раньше 01.10 00:00».
    """
    clauses = ['TRUE']
    if filters.get('date_from'):
        clauses.append('i.created_at >= %(date_from)s')
        params['date_from'] = datetime.combine(filters['date_from'], datetime.min.time())
    if filters.get('date_to'):
        clauses.append('i.created_at < %(date_to_next)s')
        params['date_to_next'] = datetime.combine(
            filters['date_to'] + timedelta(days=1), datetime.min.time())
    if filters.get('city'):
        clauses.append('LOWER(TRIM(i.city)) = LOWER(TRIM(%(city)s))')
        params['city'] = filters['city']
    if filters.get('water_office_id'):
        clauses.append('i.water_office_id = %(water_office_id)s')
        params['water_office_id'] = int(filters['water_office_id'])
    if filters.get('issued_by'):
        clauses.append('i.issued_by = %(issued_by)s')
        params['issued_by'] = int(filters['issued_by'])
    if filters.get('park'):
        clauses.append('i.driver_park = %(park)s')
        params['park'] = filters['park']
    if filters.get('kind'):
        clauses.append('i.kind = %(kind)s')
        params['kind'] = filters['kind']
    query = str(filters.get('query') or '').strip()
    if query:
        # Телефон ищем по цифрам: «+7 700 000-00-01» и «87000000001» — один
        # номер, а в базе он лежит как вернула CRM.
        digits = ''.join(ch for ch in query if ch.isdigit())
        parts = ['i.driver_name ILIKE %(q_like)s', 'i.driver_account_id ILIKE %(q_like)s']
        params['q_like'] = '%%%s%%' % query.replace('%', r'\%').replace('_', r'\_')
        if len(digits) >= 4:
            parts.append("regexp_replace(COALESCE(i.driver_phone, ''), '\\D', '', 'g') "
                         "LIKE %(q_digits)s")
            params['q_digits'] = '%%%s%%' % digits[-10:]
        clauses.append('(%s)' % ' OR '.join(parts))
    return ' AND '.join(clauses)


def _select_issues(cursor, filters, *, limit, offset):
    """(строки, всего по отбору). Счётчик — отдельным COUNT, а не окном
    COUNT(*) OVER (): за последней страницей окно считает ноль."""
    params = {}
    where = _issue_filter(filters, params)
    cursor.execute('SELECT COUNT(*) FROM water_issues i WHERE %s' % where, params)
    total = int(cursor.fetchone()[0] or 0)
    params.update(limit=int(limit), offset=max(0, int(offset)))
    cursor.execute(
        'SELECT %s FROM water_issues i WHERE %s ORDER BY i.created_at DESC, i.id DESC '
        'LIMIT %%(limit)s OFFSET %%(offset)s' % (
            ', '.join('i.%s' % name for name in _ISSUE_COLUMNS), where),
        params)
    return [_issue_row(row) for row in cursor.fetchall()], total


def list_issues(cursor, filters, *, limit=50, offset=0):
    return _select_issues(cursor, filters, limit=max(1, min(int(limit), 200)), offset=offset)


def issues_for_export(cursor, filters, *, limit):
    return _select_issues(cursor, filters, limit=limit, offset=0)


def filter_values(cursor):
    """Значения фильтров журнала, которые реально встречаются."""
    cursor.execute(
        "SELECT DISTINCT issued_by, issued_by_name FROM water_issues "
        "WHERE issued_by IS NOT NULL ORDER BY issued_by_name")
    staff = {}
    for user_id, name in cursor.fetchall():
        staff.setdefault(user_id, name or '№%s' % user_id)
    cursor.execute(
        "SELECT DISTINCT driver_park FROM water_issues "
        "WHERE COALESCE(TRIM(driver_park), '') <> '' ORDER BY driver_park")
    parks = [row[0] for row in cursor.fetchall()]
    return {
        'staff': [{'id': user_id, 'name': name} for user_id, name in
                  sorted(staff.items(), key=lambda item: str(item[1]).lower())],
        'parks': parks,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Остатки по офисам (дашборд)
# ─────────────────────────────────────────────────────────────────────────────

def dashboard(cursor, settings, *, date_from, date_to, today):
    """Остатки и расход по офисам за период.

    Средний расход — выдано за период / дней периода, и дни считаются только те,
    когда офис уже был в учёте и которые уже наступили: офис, заведённый три дня
    назад, иначе делил бы свои три дня выдач на тридцать и «хватало» бы ему
    вдесятеро дольше, чем на самом деле.

    «Поступило» — поступления плюс разницы пересчётов, отмеченных
    `adjusts_intake`: внесли 500, которых ещё не привезли, и сняли их
    пересчётом — остаток стал верным, а «Поступило» без отметки так и
    показывало бы 500 (05.10.2026). Разница со знаком и стоит в день пересчёта,
    так что за узкий период, где есть только исправление, выйдет минус.
    Обычный пересчёт (недостача, излишек, стартовый остаток) сюда не входит.
    """
    params = {
        'from': datetime.combine(date_from, datetime.min.time()),
        'to_next': datetime.combine(date_to + timedelta(days=1), datetime.min.time()),
    }
    cursor.execute(
        """
        SELECT o.id, o.city, o.name, o.address, o.stock, o.low_threshold, o.buy_threshold, o.created_at,
               COALESCE(i.blocks, 0), COALESCE(i.issues, 0), COALESCE(i.welcome, 0),
               COALESCE(i.activity, 0), COALESCE(m.intake, 0)
          FROM water_offices o
          LEFT JOIN (
                SELECT water_office_id, SUM(blocks) AS blocks, COUNT(*) AS issues,
                       COUNT(*) FILTER (WHERE kind = 'welcome') AS welcome,
                       COUNT(*) FILTER (WHERE kind = 'activity') AS activity
                  FROM water_issues
                 WHERE created_at >= %(from)s AND created_at < %(to_next)s
                   AND canceled_at IS NULL
                 GROUP BY water_office_id
          ) i ON i.water_office_id = o.id
          LEFT JOIN (
                SELECT water_office_id, SUM(delta) AS intake
                  FROM water_movements
                 WHERE (kind = 'intake' OR adjusts_intake)
                   AND created_at >= %(from)s AND created_at < %(to_next)s
                 GROUP BY water_office_id
          ) m ON m.water_office_id = o.id
         WHERE o.is_active
         ORDER BY o.city, o.name, o.id
        """,
        params)
    rows = []
    for row in cursor.fetchall():
        (office_id, city, name, address, stock, own_low, own_buy, created_at, issued, issues,
         welcome, activity, intake) = row
        low = own_low if own_low is not None else settings['low_threshold']
        buy = own_buy if own_buy is not None else settings['buy_threshold']
        start = max(date_from, rules.day_of(created_at) or date_from)
        end = min(date_to, today)
        days = max(1, (end - start).days + 1)
        avg = float(issued) / days
        rows.append({
            'id': office_id, 'city': city, 'name': name, 'address': address, 'stock': int(stock),
            'low_threshold': low, 'buy_threshold': buy,
            'status': rules.stock_status(stock, low, buy),
            'issued_blocks': int(issued), 'issues': int(issues),
            'welcome': int(welcome), 'activity': int(activity), 'intake_blocks': int(intake),
            'avg_daily': round(avg, 2), 'avg_weekly': round(avg * 7, 1), 'days': days,
            'days_left': int(stock // avg) if avg > 0 else None,
            # Вверх, а не вниз: пока остаток выше порога, закупка — не «через
            # ~0 дней», а завтра. Ноль — только у тех, кто уже на пороге.
            'days_to_buy': (math.ceil((stock - buy) / avg) if avg > 0 and stock > buy
                            else (0 if stock <= buy else None)),
        })
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Уведомления «Требуется закупка»
# ─────────────────────────────────────────────────────────────────────────────

def front_office_head_ids(cursor):
    cursor.execute(
        "SELECT head_user_id FROM departments "
        "WHERE code = 'front_office' AND is_active AND head_user_id IS NOT NULL")
    return [row[0] for row in cursor.fetchall()]


def notify_candidates(cursor):
    """Кого можно выбрать получателем «Требуется закупка»: тех, кто воду выдаёт
    или ведёт учёт (access.can_issue / can_manage) — супер-админов, руководителя
    фронт-офисов и офисников из списка. Колл-центр закупку не ведёт, а админам
    других отделов раздел закрыт — письмо со ссылкой в закрытый экран им слать
    незачем.

    Отбор — теми же правилами, что и вход в раздел, а не своей копией в SQL:
    разойдись они, человек получал бы письмо и упирался в отказ.
    """
    cursor.execute(
        """
        SELECT u.id, u.name, u.city, u.telegram_id IS NOT NULL, u.role, d.code,
               COALESCE((SELECT array_agg(h.id) FROM departments h
                          WHERE h.head_user_id = u.id AND h.is_active), '{}'),
               COALESCE((SELECT array_agg(h.code) FROM departments h
                          WHERE h.head_user_id = u.id AND h.is_active), '{}')
          FROM users u
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE COALESCE(u.status, '') <> 'fired'
           AND (u.role = 'super_admin'
                OR d.code = 'front_office'
                OR u.id IN (SELECT head_user_id FROM departments
                             WHERE code = 'front_office' AND is_active
                               AND head_user_id IS NOT NULL))
         ORDER BY u.name
        """)
    people = []
    for (user_id, name, city, has_telegram, role, department_code,
         headed_ids, headed_codes) in cursor.fetchall():
        # Тот же контекст, что собирает load_access_context для входа в раздел.
        ctx = {
            'user_id': user_id, 'role': access.normalize_role(role),
            'department_code': department_code,
            'headed_department_ids': list(headed_ids or []),
            'headed_department_codes': list(headed_codes or []),
        }
        if access.can_issue(ctx) or access.can_manage(ctx):
            people.append({'id': user_id, 'name': name, 'city': city,
                           'has_telegram': bool(has_telegram)})
    return people


def notify_recipients(cursor, settings):
    """[{user_id, name, chat_id}] — кому уходит «Требуется закупка».

    Список из настроек; пустой — пишем главе фронт-офисов. У кого нет Telegram,
    тот в выборку не попадает: слать некуда.
    """
    def deliverable(ids):
        if not ids:
            return []
        cursor.execute(
            "SELECT id, name, telegram_id FROM users "
            "WHERE id = ANY(%s) AND telegram_id IS NOT NULL AND COALESCE(status, '') <> 'fired'",
            (sorted({int(x) for x in ids}),))
        return [{'user_id': row[0], 'name': row[1], 'chat_id': row[2]}
                for row in cursor.fetchall()]

    # Выбранных уволили или у них нет Telegram — это не повод промолчать о
    # закупке: пишем главе, как если бы список был пуст.
    return (deliverable(settings.get('notify_user_ids') or [])
            or deliverable(front_office_head_ids(cursor)))
