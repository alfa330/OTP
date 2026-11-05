# -*- coding: utf-8 -*-
"""«Учет сотрудников» — списки по страницам: разбор запроса и SQL.

Пять списков раздела («Сотрудники», «Супервайзеры», «Тренеры», «Админы» и
«Операторы» у СВ и тренера) раньше приходили целиком (/api/admin/users,
/api/admin/sv_list), а метки статусов, поиск, порядок и страницы считал
браузер. Здесь то же самое делает база: фильтр, порядок и LIMIT/OFFSET — в SQL,
наружу уходит одна страница, числа на метках статусов и, у «Операторов»,
итоги групп «Мои операторы» и направлений.

Правила списка — те же, что были в браузере (App.jsx, employeesPhoneList.js):
  • вкладка статуса — normalizeEmployeeStatusCode: пусто — «Работает»,
    unpaid_leave — старое имя Б/С, «Увольнение» лежит в «Уволенных»;
  • поиск — подстрока без учёта регистра в имени, направлении, должности и
    имени супервайзера; у «Операторов» направление без названия ищется как
    «Без направления» — так оно подписано в шапке группы;
  • порядок — по полю, при равенстве по имени от А к Я, последним — id, чтобы
    страницы не теряли и не повторяли людей с одинаковыми именами;
  • числа на метках считаются по отделу, но без поиска — как раньше.

Кого видно — решает routes.py теми же границами, что у полных ручек; полные
ручки остаются: их читают другие разделы портала.

Функции принимают готовый курсор и ничего не пишут.
"""

from dataclasses import dataclass
from typing import Optional

# Строк на странице — как EMPLOYEES_PAGE_SIZE в src/components/employees/employeesPaging.js.
PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
# «Выбрать всех» на телефоне берёт id всех найденных одним запросом.
MAX_IDS = 5000
MAX_QUERY_LENGTH = 200

STATUS_TABS = ('active', 'working', 'bs', 'sick_leave', 'annual_leave', 'fired')
SORT_FIELDS = ('name', 'direction', 'supervisor', 'status', 'hire_date', 'rate')
LISTS = ('users', 'supervisors')
GROUPINGS = ('', 'operators')

# Шапка группы оператора без направления — getOperatorDirectionLabel в App.jsx.
NO_DIRECTION = 'Без направления'


class PageError(ValueError):
    """Неверный параметр запроса — ответ 400 с текстом."""


@dataclass(frozen=True)
class PageQuery:
    list: str = 'users'
    roles: Optional[tuple] = None
    status: str = 'active'
    department_id: Optional[int] = None
    search: str = ''
    sort: str = 'name'
    direction: str = 'asc'
    grouping: str = ''
    limit: int = PAGE_SIZE
    offset: int = 0
    with_ids: bool = False


@dataclass(frozen=True)
class Scope:
    """Кого видит смотрящий. roles — точные значения users.role (у «users»);
    headed — отделы главы; sv_department — граница СВ: свой отдел плюс те,
    кого он ведёт сам (supervisor_id = requester_id)."""
    list: str
    roles: tuple = ()
    headed: Optional[tuple] = None
    sv_department: Optional[int] = None
    requester_id: Optional[int] = None


def _int(value, name, *, minimum=None, maximum=None, default=None):
    if value in (None, ''):
        return default
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise PageError(f'{name}: нужно целое число') from None
    if minimum is not None and number < minimum:
        raise PageError(f'{name}: не меньше {minimum}')
    if maximum is not None and number > maximum:
        number = maximum
    return number


def _choice(value, name, allowed, default):
    text = str(value or '').strip().lower()
    if not text:
        return default
    if text not in allowed:
        raise PageError(f'{name}: одно из {", ".join(item for item in allowed if item)}')
    return text


def parse_query(args):
    """Параметры ?list&roles&status&department&q&sort&dir&group&limit&offset&ids."""
    roles = None
    raw_roles = args.get('roles')
    if raw_roles is not None:
        roles = tuple(sorted({part.strip().lower() for part in str(raw_roles).split(',') if part.strip()}))
    # NUL Postgres в строку не берёт (500 вместо ответа); в именах его не бывает.
    search = str(args.get('q') or '').replace('\x00', '').strip()
    if len(search) > MAX_QUERY_LENGTH:
        search = search[:MAX_QUERY_LENGTH]
    list_name = _choice(args.get('list'), 'list', LISTS, 'users')
    grouping = _choice(args.get('group'), 'group', GROUPINGS, '')
    if grouping and list_name != 'users':
        raise PageError('group: только у list=users')
    return PageQuery(
        list=list_name,
        roles=roles,
        status=_choice(args.get('status'), 'status', STATUS_TABS, 'active'),
        department_id=_int(args.get('department'), 'department', minimum=1),
        search=search,
        sort=_choice(args.get('sort'), 'sort', SORT_FIELDS, 'name'),
        direction=_choice(args.get('dir'), 'dir', ('asc', 'desc'), 'asc'),
        grouping=grouping,
        limit=_int(args.get('limit'), 'limit', minimum=1, maximum=MAX_PAGE_SIZE, default=PAGE_SIZE),
        offset=_int(args.get('offset'), 'offset', minimum=0, default=0),
        with_ids=str(args.get('ids') or '').strip() in ('1', 'true'),
    )


# ─────────────────────────── SQL списка ───────────────────────────

# normalizeEmployeeStatusCode в App.jsx.
_STATUS_CODE_SQL = """CASE lower(btrim(COALESCE(u.status, '')))
                WHEN '' THEN 'working'
                WHEN 'unpaid_leave' THEN 'bs'
                ELSE lower(btrim(COALESCE(u.status, '')))
            END"""

# Порядок «По статусу» — как метки над списком: работает, Б/С, больничный,
# отпуск, увольнение, уволен.
_STATUS_RANK_SQL = """CASE status_code
                WHEN 'working' THEN 1 WHEN 'bs' THEN 2 WHEN 'sick_leave' THEN 3
                WHEN 'annual_leave' THEN 4 WHEN 'dismissal' THEN 5 WHEN 'fired' THEN 6
                ELSE 7
            END"""


# Ключ порядка текста — как localeCompare('ru', {sensitivity: 'base'}) в
# браузере, без ICU в базе (её наличие на проде не проверено, а COLLATE "C"
# есть везде): сравнение строчных букв по кодам символов. Кириллица а–я в кодах
# уже стоит по алфавиту, «ё» — та же «е», а казахские буквы встают сразу за
# своей основой (а ә б … г ғ … и і й … к қ … н ң … о ө … у ү ұ … х һ): к основе
# приписывается знак за пределами алфавита. Латиница у браузерного 'ru' идёт
# после кириллицы в любом месте строки — её буквы сдвигаются за кириллицу
# (U+0500…: в именах таких знаков нет). Пробел и дефис в кодах раньше букв —
# как в браузере, «Иванов Иван» раньше «Иванова Анна» (сортировка базы по
# умолчанию пробелы не считает и ставит наоборот).
_PAST_LETTER = 'chr(65532)'
_SORT_LETTERS = (
    ('ё', "'е'"),
    ('ә', f"'а' || {_PAST_LETTER}"),
    ('ғ', f"'г' || {_PAST_LETTER}"),
    ('і', f"'и' || {_PAST_LETTER}"),
    ('қ', f"'к' || {_PAST_LETTER}"),
    ('ң', f"'н' || {_PAST_LETTER}"),
    ('ө', f"'о' || {_PAST_LETTER}"),
    ('ү', f"'у' || {_PAST_LETTER}"),
    ('ұ', "'у' || chr(65533)"),
    ('һ', f"'х' || {_PAST_LETTER}"),
)
_LATIN = 'abcdefghijklmnopqrstuvwxyz'
_LATIN_AFTER_CYRILLIC = ''.join(chr(0x0500 + index) for index in range(len(_LATIN)))


def sort_key_sql(expr):
    sql = f'lower({expr})'
    for letter, replacement in _SORT_LETTERS:
        sql = f"replace({sql}, '{letter}', {replacement})"
    return f"translate({sql}, '{_LATIN}', '{_LATIN_AFTER_CYRILLIC}')"


def _scope_sql(scope):
    if scope.list == 'supervisors':
        clauses = ["LOWER(COALESCE(u.role, '')) IN ('sv', 'supervisor')"]
    else:
        clauses = ['u.role = ANY(%(roles)s)']
    params = {'roles': list(scope.roles)}
    if scope.headed is not None:
        clauses.append('u.department_id = ANY(%(headed)s)')
        params['headed'] = [int(item) for item in scope.headed]
    elif scope.sv_department is not None:
        # Как в /api/admin/users: свой отдел и все, кого ведёт сам, даже из
        # другого отдела или без отдела.
        clauses.append('(u.department_id = %(sv_department)s OR u.supervisor_id = %(scope_requester)s)')
        params['sv_department'] = int(scope.sv_department)
        params['scope_requester'] = int(scope.requester_id)
    return clauses, params


def _keyed_cte(query, scope, me):
    """CTE base → keyed: по строке на видимого человека, с ключами фильтра и порядка."""
    clauses, params = _scope_sql(scope)
    if query.department_id is not None:
        clauses.append('u.department_id = %(department)s')
        params['department'] = int(query.department_id)
    grouped = query.grouping == 'operators'
    if scope.list == 'supervisors':
        # Строки «Супервайзеров» приходят без направления и СВ (как в
        # /api/admin/sv_list): поиск и порядок по ним в браузере шли по пустому.
        joins = ''
        direction_name = "''"
        supervisor_name = "''"
    else:
        joins = """
            LEFT JOIN directions d ON d.id = u.direction_id
            LEFT JOIN users s ON s.id = u.supervisor_id"""
        direction_name = "COALESCE(d.name, '')"
        supervisor_name = "COALESCE(s.name, '')"
    if grouped:
        group_label = "COALESCE(NULLIF(d.name, ''), %(no_direction)s)"
        params['no_direction'] = NO_DIRECTION
        params['me'] = int(me)
        is_mine = 'COALESCE(u.supervisor_id = %(me)s, FALSE)'
        direction_search = f'lower({group_label})'
    else:
        group_label = "''"
        is_mine = 'FALSE'
        direction_search = f'lower({direction_name})'
    sql = f"""
        WITH base AS (
            SELECT u.id,
                   {_STATUS_CODE_SQL} AS status_code,
                   lower(COALESCE(u.name, '')) AS name_search,
                   {sort_key_sql("COALESCE(u.name, '')")} AS name_sort,
                   {direction_search} AS direction_search,
                   {sort_key_sql(direction_name)} AS direction_sort,
                   lower({supervisor_name}) AS supervisor_search,
                   {sort_key_sql(supervisor_name)} AS supervisor_sort,
                   lower(COALESCE(u.job_title, '')) AS job_title_search,
                   u.hire_date,
                   COALESCE(u.rate, 0) AS rate_key,
                   {is_mine} AS is_mine,
                   {group_label} AS group_label
              FROM users u{joins}
             WHERE {' AND '.join(clauses)}
        ),
        keyed AS (
            SELECT base.*,
                   CASE status_code WHEN 'dismissal' THEN 'fired' ELSE status_code END AS status_tab,
                   {_STATUS_RANK_SQL} AS status_rank,
                   -- Свои — одна группа, какое бы направление у каждого ни стояло.
                   CASE WHEN is_mine THEN '' ELSE group_label END AS list_label,
                   {sort_key_sql("CASE WHEN is_mine THEN '' ELSE group_label END")} AS list_sort
              FROM base
        )"""
    return sql, params


def _hits_sql(query, params):
    """Фильтр вкладки статуса и поиска поверх keyed."""
    clauses = []
    if query.status == 'active':
        clauses.append("status_tab <> 'fired'")
    else:
        clauses.append('status_tab = %(status)s')
        params['status'] = query.status
    if query.search:
        params['q'] = query.search
        clauses.append("""(strpos(name_search, lower(%(q)s)) > 0
                   OR strpos(direction_search, lower(%(q)s)) > 0
                   OR strpos(job_title_search, lower(%(q)s)) > 0
                   OR strpos(supervisor_search, lower(%(q)s)) > 0)""")
    return f"""hits AS (SELECT * FROM keyed WHERE {' AND '.join(clauses)})"""


def _text(expr, direction):
    """Ключ sort_key_sql — по кодам символов."""
    return f"({expr}) COLLATE \"C\" {direction}"


def _group_order(query):
    order = 'DESC' if query.direction == 'desc' and query.sort == 'direction' else 'ASC'
    # Направления, которые различаются только регистром или е/ё, — разные группы:
    # без точного имени последним ключом их строки шли бы вперемешку.
    return ['is_mine DESC', _text('list_sort', order), f'list_label COLLATE "C" {order}']


def order_by(query, list_name):
    d = 'DESC' if query.direction == 'desc' else 'ASC'
    name_asc = _text('name_sort', 'ASC')
    sort = query.sort
    if list_name == 'supervisors' and sort in ('direction', 'supervisor'):
        sort = None
    if query.grouping == 'operators':
        # «По направлению» переставляет сами группы, а внутри идёт по имени в
        # ту же сторону; любой другой порядок — внутри группы, а группы по
        # алфавиту (groupOperators в employeesPhoneList.js).
        if sort == 'direction':
            within = [_text('name_sort', d)]
        else:
            within = _primary(sort, d, name_asc)
        parts = _group_order(query) + within
    else:
        parts = _primary(sort, d, name_asc)
    return ', '.join(parts + ['id ASC'])


def _primary(sort, d, name_asc):
    if sort == 'name':
        return [_text('name_sort', d)]
    if sort == 'direction':
        return [_text('direction_sort', d), name_asc]
    if sort == 'supervisor':
        return [_text('supervisor_sort', d), name_asc]
    if sort == 'status':
        return [f'status_rank {d}', name_asc]
    if sort == 'hire_date':
        # Без даты найма — как 0 в браузере: первыми от старых, последними от новых.
        return [f"hire_date {d} NULLS {'FIRST' if d == 'ASC' else 'LAST'}", name_asc]
    if sort == 'rate':
        return [f'rate_key {d}', name_asc]
    return [name_asc]


def status_counts(rows):
    """[(status_tab, n)] → числа на метках: «Активные» — все, кроме уволенных."""
    by_tab = {str(tab): int(count) for tab, count in rows}
    total = sum(by_tab.values())
    counts = {tab: by_tab.get(tab, 0) for tab in STATUS_TABS}
    counts['active'] = total - by_tab.get('fired', 0)
    return counts, total


def clamp_offset(offset, limit, total):
    """Страница за концом выборки (людей стало меньше) — последняя непустая."""
    if total <= 0:
        return 0
    if offset < total:
        return offset - offset % limit
    return ((total - 1) // limit) * limit


def group_key_of(is_mine, label):
    return 'mine' if is_mine else f'dir:{label}'


def fetch_page(cursor, query, scope, *, me, load_rows):
    """Страница списка. load_rows(cursor, list, ids) → строки в форме полной ручки."""
    if scope.list != 'supervisors' and not scope.roles:
        return _empty(query)

    keyed_sql, params = _keyed_cte(query, scope, me)

    cursor.execute(f'{keyed_sql} SELECT status_tab, COUNT(*) FROM keyed GROUP BY status_tab', params)
    counts, list_total = status_counts(cursor.fetchall())

    hits_params = dict(params)
    hits_sql = _hits_sql(query, hits_params)
    head = f'{keyed_sql}, {hits_sql}'

    groups = None
    if query.grouping == 'operators':
        group_order = ', '.join(_group_order(query))
        cursor.execute(f"""{head}
            SELECT is_mine, list_label, COUNT(*) FROM hits
             GROUP BY is_mine, list_label, list_sort
             ORDER BY {group_order}""", hits_params)
        groups = [
            {'key': group_key_of(is_mine, label), 'title': 'Мои операторы' if is_mine else label, 'total': int(count)}
            for is_mine, label, count in cursor.fetchall()
        ]
        total = sum(group['total'] for group in groups)
    else:
        cursor.execute(f'{head} SELECT COUNT(*) FROM hits', hits_params)
        total = int(cursor.fetchone()[0])

    order = order_by(query, scope.list)
    offset = clamp_offset(query.offset, query.limit, total)
    page_params = dict(hits_params, limit=query.limit, offset=offset)
    cursor.execute(f"""{head}
        SELECT id, is_mine, list_label FROM hits
         ORDER BY {order}
         LIMIT %(limit)s OFFSET %(offset)s""", page_params)
    page_rows = cursor.fetchall()
    ids = [int(row[0]) for row in page_rows]

    by_id = {int(item['id']): item for item in load_rows(cursor, scope.list, ids)} if ids else {}
    employees = []
    for row_id, is_mine, label in page_rows:
        item = by_id.get(int(row_id))
        if item is None:
            continue
        if groups is not None:
            item = dict(item, list_group=group_key_of(is_mine, label))
        employees.append(item)

    result = {
        'employees': employees,
        'total': total,
        'list_total': list_total,
        'offset': offset,
        'limit': query.limit,
        'status_counts': counts,
    }
    if groups is not None:
        result['groups'] = groups
    if query.with_ids:
        ids_params = dict(hits_params, max_ids=MAX_IDS)
        cursor.execute(f'{head} SELECT id FROM hits ORDER BY {order} LIMIT %(max_ids)s', ids_params)
        result['ids'] = [int(row[0]) for row in cursor.fetchall()]
    return result


def _empty(query):
    counts = {tab: 0 for tab in STATUS_TABS}
    result = {'employees': [], 'total': 0, 'list_total': 0, 'offset': 0, 'limit': query.limit,
              'status_counts': counts}
    if query.grouping == 'operators':
        result['groups'] = []
    if query.with_ids:
        result['ids'] = []
    return result
