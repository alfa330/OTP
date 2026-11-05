# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: EMPLOYEES_PAGE_TEST_PORT — порт локального Postgres.

«Учет сотрудников» по страницам (employees/page.py, GET /api/admin/employees/page).
На одних и тех же людях сравниваются полные ручки — get_admin_users и get_sv_list,
их код достаётся из монолита через ast и выполняется на этом же Postgres, — и
страницы новой ручки через настоящий Flask:
  • кого видно — тот же набор людей у каждого вида смотрящего;
  • строка страницы равна строке полного списка поле в поле;
  • вкладки статусов, числа на них, поиск, отдел и порядок — как считал браузер
    (App.jsx: isEmployeeVisibleByStatusTab, matchesEmployeeSearchQuery,
    compareUsersByField, группы «Операторов»);
  • страницы не теряют и не повторяют людей, страница за концом — последняя.

Порядок имён сверяется с localeCompare('ru') браузера: BROWSER_NAME_ORDER ниже
снят в node (`[...].sort((a, b) => a.localeCompare(b, 'ru', {sensitivity: 'base'}))`).

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема в одной
транзакции, в конце откат. Запуск на стенде из `.claude/skills/verify`:

    EMPLOYEES_PAGE_TEST_PORT=55433 PGPASSWORD=verify python -m pytest tests/test_employees_page_postgres.py
"""
import ast
import copy
import functools
import logging
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlencode

import pytest

from tests import source_cache

PORT = os.environ.get('EMPLOYEES_PAGE_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (EMPLOYEES_PAGE_TEST_PORT)')

BOT_PATH = source_cache.monolith_flat_path()
BACK_OFFICE = frozenset({'accounting_manager', 'hr_manager', 'marketing_manager'})
RANK_AND_FILE = frozenset({'operator', 'trainee', *BACK_OFFICE})
PROXY_LABELS = {'issued': 'Выдана'}
STATUS_TABS = ('active', 'working', 'bs', 'sick_leave', 'annual_leave', 'fired')

# Латинская буква-двойник внутри кириллического имени — частая опечатка набора.
LATIN_E_NAME = 'Ахм' + 'e' + 'тов Ерлан'  # «e» латинская
LATIN_O_NAME = 'Иван' + 'o' + 'в Олег'  # «o» латинская

# Имена людей ниже, отсортированные браузером: localeCompare('ru', {sensitivity: 'base'}).
BROWSER_NAME_ORDER = [
    'Аристов Арман', 'Ахметов Бауыржан', 'Ахметова Айгерим', LATIN_E_NAME, 'Әлия Нұрланқызы', 'Бухгалтер Бухова',
    'Елизаров Ефим', 'Ёлкин Пётр', 'Иванов Иван', 'Иванов Иван', 'Иванов-Петров Олег', 'Иванова Анна', LATIN_O_NAME,
    'Ілияс Ғалымов', 'Йодов Яков', 'Кадровик Кадрова', 'Ким А', 'Кима Б', 'Қасым Ұлан', 'Маркетолог Мария',
    'Оператор Без Отдела', 'Оператор Ничей', 'Оператор ОП Два', 'Оператор ОП Один', 'Оператор Чужой',
    'Стажёр Ольга', 'Стажёр Сергей', 'Үмбетов Һасан', 'Ұлжан Үсенова', 'Яковлев Ян', 'andrew Lee', 'John Smith',
]

SCHEMA = '''
    CREATE TABLE departments (id integer PRIMARY KEY, name text, code text);
    CREATE TABLE directions (id integer PRIMARY KEY, name text);
    CREATE TABLE groups (id integer PRIMARY KEY, name text);
    CREATE TABLE users (
        id integer PRIMARY KEY, name text, role text, status text, rate numeric, hire_date date,
        gender text, birth_date date, avatar_bucket text, avatar_blob_path text, avatar_updated_at timestamp,
        phone text, email text, personal_email text, instagram text, telegram_nick text, company_name text,
        employment_type text, has_proxy boolean, proxy_card_number text, proxy_status text,
        has_driver_license boolean, sip_number text, study_place text, study_course text,
        close_contact_1_relation text, close_contact_1_full_name text, close_contact_1_phone text,
        close_contact_2_relation text, close_contact_2_full_name text, close_contact_2_phone text,
        card_number text, internship_in_company boolean, front_office_training boolean,
        front_office_training_date date, taxipro_id text, study_completed boolean, study_completion_year integer,
        department_id integer, direction_id integer, supervisor_id integer, city text, job_title text,
        study_specialty text, hours_table_url text
    );
    CREATE TABLE group_operator_memberships (
        id serial PRIMARY KEY, group_id integer, operator_id integer, start_date date, end_date date
    );
    CREATE TABLE operator_schedule_status_periods (
        id serial PRIMARY KEY, operator_id integer, status_code text, start_date date, end_date date,
        dismissal_reason text, is_blacklist boolean, comment text
    );
    INSERT INTO departments VALUES (1, 'Колл-центр', 'cc'), (2, 'ОП', 'op'), (3, 'HR', 'hr'),
                                   (4, 'Бухгалтерия', 'accounting'), (5, 'Маркетинг', 'marketing');
    INSERT INTO directions VALUES (10, 'Яндекс Еда'), (11, 'Доставка'), (12, 'Ақтөбе линия'), (13, ''),
                                  (14, 'яндекс еда');
    INSERT INTO groups VALUES (100, 'Группа А'), (101, 'Группа Б');
'''

# id, имя, роль, статус, отдел, направление, СВ, дата найма, ставка, должность
PEOPLE = [
    (1, 'Сабина СВ', 'sv', 'working', 1, None, None, '2021-01-01', 1, None),
    (2, 'Олжас СВ', 'sv', 'annual_leave', 2, None, None, None, 1, None),
    (3, 'Без Отдела СВ', 'sv', 'working', None, None, None, '2020-06-01', 1, None),
    (4, 'Supervisor Latin', 'supervisor', 'fired', 1, None, None, '2019-01-01', 1, None),
    (10, 'Лазурный Супервайзер', 'supervisor', 'working', 1, None, None, '2022-05-05', 1, None),
    (5, 'Админ Главный', 'admin', 'working', None, None, None, None, 1, None),
    (6, 'Админ Второй', 'admin', 'dismissal', 2, None, None, None, 1, None),
    (7, 'Супер Админ', 'super_admin', 'working', None, None, None, None, 1, None),
    (8, 'Тренер Тимур', 'trainer', 'working', 1, None, None, '2022-01-01', 1, None),
    (9, 'Тренер Әсел', 'trainer', 'annual_leave', 2, None, None, None, 0.5, None),
    (20, 'Кадровик Кадрова', 'hr_manager', 'working', 3, None, None, '2023-01-01', 1, 'Кадровик'),
    (21, 'Бухгалтер Бухова', 'accounting_manager', 'bs', 4, None, None, None, 1, 'Главный бухгалтер'),
    (22, 'Маркетолог Мария', 'marketing_manager', 'working', 5, None, None, '2024-04-04', 0.75, 'Таргетолог'),
    (30, 'Иванов Иван', 'operator', 'working', 1, 10, 1, '2024-01-10', 1, None),
    (31, 'Иванова Анна', 'operator', None, 1, 10, 1, '2023-05-01', 0.75, None),
    (32, 'Иванов-Петров Олег', 'operator', 'unpaid_leave', 1, 11, 1, None, 0.5, None),
    (33, 'Ёлкин Пётр', 'operator', 'sick_leave', 1, 11, 3, '2022-02-02', 1, None),
    (34, 'Елизаров Ефим', 'operator', 'annual_leave', 1, None, None, '2025-03-03', 1, None),
    (35, 'Әлия Нұрланқызы', 'operator', 'working', 1, 12, 1, '2024-01-10', 0.75, None),
    (36, 'Ахметов Бауыржан', 'operator', 'fired', 1, 10, 1, '2020-01-01', 1, None),
    (37, 'Ахметова Айгерим', 'operator', 'dismissal', 1, 10, 1, '2020-01-01', 1, None),
    (38, 'John Smith', 'operator', 'working', 1, 10, 1, '2024-06-01', 1, None),
    (39, 'Қасым Ұлан', 'operator', 'working', 1, 12, 2, '2024-06-01', 0.5, None),
    (40, 'Ілияс Ғалымов', 'operator', ' Working ', 1, 11, 1, None, 1, None),
    (41, 'Үмбетов Һасан', 'operator', 'bs', 1, 13, 1, '2021-07-07', 1, None),
    (42, 'Иванов Иван', 'operator', 'working', 1, 11, 1, '2024-01-10', 1, None),
    (43, 'Ұлжан Үсенова', 'operator', 'working', 1, None, 1, '2023-03-03', 1, None),
    (44, 'Йодов Яков', 'operator', 'working', 1, 10, 3, '2023-03-03', 1, None),
    (45, 'Ким А', 'operator', 'working', 1, 11, 1, '2023-03-03', 1, None),
    (46, 'Кима Б', 'operator', 'working', 1, 11, 1, '2023-03-03', 1, None),
    (47, 'andrew Lee', 'operator', 'working', 1, 11, 1, '2023-03-03', 1, None),
    (48, LATIN_E_NAME, 'operator', 'working', 1, 10, 1, '2023-03-03', 1, None),
    (49, LATIN_O_NAME, 'operator', 'working', 1, 11, 1, '2023-03-03', 1, None),
    # «яндекс еда» (14) отличается от «Яндекс Еда» (10) только регистром — две группы,
    # и строки каждой идут сплошным блоком.
    (53, 'Аристов Арман', 'operator', 'working', 1, 14, 3, '2023-03-03', 1, None),
    (54, 'Яковлев Ян', 'operator', 'working', 1, 14, 3, '2023-03-03', 1, None),
    (50, 'Оператор ОП Один', 'operator', 'working', 2, 10, 2, '2024-02-02', 1, None),
    (51, 'Оператор ОП Два', 'operator', 'fired', 2, None, 2, '2024-02-03', 1, None),
    (52, 'Оператор Чужой', 'operator', 'working', 2, 11, 1, '2024-02-04', 1, None),
    (60, 'Оператор Без Отдела', 'operator', 'working', None, None, 1, '2024-02-05', 1, None),
    (61, 'Оператор Ничей', 'operator', 'working', None, None, None, '2024-02-06', 1, None),
    (70, 'Стажёр Сергей', 'trainee', 'working', 1, 10, 1, '2025-01-01', 1, None),
    (71, 'Стажёр Ольга', 'trainee', 'fired', 2, None, None, '2025-01-02', 1, None),
    # Роль с заглавной: полные ручки сравнивают роль точно, и такого человека нет нигде.
    (80, 'Оператор Заглавный', 'Operator', 'working', 1, 10, 1, None, 1, None),
]


@dataclass
class Viewer:
    """Смотрящий: то, что монолит узнал бы о нём из базы."""
    key: str
    id: int
    role: str
    headed: frozenset = field(default_factory=frozenset)
    scope_dept: int = None
    hr: bool = False


VIEWERS = [
    Viewer('super_admin', 7, 'super_admin'),
    Viewer('super_admin_head', 7, 'super_admin', headed=frozenset({1})),
    Viewer('admin', 5, 'admin'),
    Viewer('admin_head', 6, 'admin', headed=frozenset({2})),
    Viewer('sv_dept1', 1, 'sv', scope_dept=1),
    Viewer('sv_no_dept', 3, 'sv'),
    Viewer('sv_head', 2, 'sv', headed=frozenset({2}), scope_dept=2),
    Viewer('trainer', 8, 'trainer', scope_dept=1),
    Viewer('hr', 20, 'hr_manager', scope_dept=3, hr=True),
    Viewer('hr_head', 20, 'hr_manager', headed=frozenset({3}), scope_dept=3, hr=True),
]
VIEWER = {viewer.key: viewer for viewer in VIEWERS}


# ─────────────────────────── код монолита ───────────────────────────

def _monolith_namespace():
    """Чистые помощники ролей из монолита — те же, что в бою."""
    tree = source_cache.tree(BOT_PATH)
    wanted = {'_normalize_user_role', '_get_role_level', '_has_min_role', '_is_admin_role',
              '_is_super_admin_role', '_is_supervisor_role', '_is_global_admin_requester'}
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            nodes.append(copy.deepcopy(node))
        elif isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == 'ROLE_HIERARCHY' for t in node.targets):
            nodes.append(copy.deepcopy(node))
    namespace = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(BOT_PATH), 'exec'), namespace)
    return namespace


def _legacy(name, namespace):
    node = source_cache.function_copy(BOT_PATH, name)
    node.decorator_list = []
    result = dict(namespace)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(BOT_PATH), 'exec'), result)
    return result[name]


def _avatar(bucket, path):
    return f'signed://{bucket}/{path}' if bucket and path else None


class _DB:
    def __init__(self, cursor):
        self.cursor = cursor

    @contextmanager
    def _get_cursor(self):
        yield self.cursor


@pytest.fixture(scope='module')
def stand():
    import psycopg2
    from flask import Flask

    conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                            password=os.environ.get('PGPASSWORD'))
    cur = conn.cursor()
    schema = 't_employees_page_' + uuid.uuid4().hex
    cur.execute('CREATE SCHEMA ' + schema)
    cur.execute('SET LOCAL search_path TO ' + schema)
    cur.execute(SCHEMA)
    for person in PEOPLE:
        pid, name, role, status, dept, direction, sv, hired, rate, job = person
        cur.execute('''
            INSERT INTO users (id, name, role, status, department_id, direction_id, supervisor_id, hire_date,
                               rate, job_title, phone, card_number, has_proxy, proxy_status, avatar_bucket,
                               avatar_blob_path, birth_date, study_completion_year, hours_table_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (pid, name, role, status, dept, direction, sv, hired, rate, job, f'+7700{pid:07d}',
              f'card-{pid}' if pid % 2 else None, pid % 3 == 0, 'issued' if pid % 4 == 0 else None,
              'bucket' if pid % 5 == 0 else None, f'avatars/{pid}.jpg' if pid % 5 == 0 else None,
              date(1990, 1 + pid % 12, 1 + pid % 27), 2015 if pid % 6 == 0 else None,
              f'https://sheet/{pid}' if role in ('sv', 'supervisor') else None))
    cur.execute('''
        INSERT INTO group_operator_memberships (group_id, operator_id, start_date, end_date) VALUES
            (100, 30, '2024-01-10', NULL), (101, 31, '2023-05-01', '2024-01-01'), (101, 31, '2024-01-02', NULL),
            (100, 35, '2024-01-10', NULL), (101, 52, '2024-02-04', NULL);
        INSERT INTO operator_schedule_status_periods
            (operator_id, status_code, start_date, end_date, dismissal_reason, is_blacklist, comment) VALUES
            (36, 'dismissal', '2025-01-01', NULL, 'Прогулы', TRUE, 'ЧС'),
            (37, 'dismissal', '2025-02-01', NULL, 'По собственному', FALSE, NULL),
            (33, 'sick_leave', CURRENT_DATE - 2, CURRENT_DATE + 2, NULL, NULL, 'Больничный лист'),
            (32, 'bs', CURRENT_DATE - 10, CURRENT_DATE + 10, NULL, NULL, NULL);
    ''')

    helpers = _monolith_namespace()
    holder = {}

    def viewer():
        return holder['viewer']

    def legacy_namespace():
        v = viewer()
        namespace = dict(helpers)
        namespace.update({
            'db': _DB(cur),
            'jsonify': lambda payload: payload,
            'logging': logging,
            'BACK_OFFICE_EMPLOYEE_ROLES': BACK_OFFICE,
            'PROXY_STATUS_LABELS': PROXY_LABELS,
            '_build_avatar_signed_url': _avatar,
            '_get_authenticated_requester': lambda: (v.id, (v.id, None, 'x', v.role), None),
            '_headed_department_ids': lambda _id: v.headed,
            '_headed_department_id': lambda _id: min(v.headed) if v.headed else None,
            '_department_scope_id_for_requester': lambda _id: v.scope_dept,
            '_is_employee_accounting_manager': lambda _id: v.hr,
            '_is_marketing_observer': lambda *_a: False,
        })
        # _is_global_admin_requester зовёт _headed_department_id из своего пространства имён.
        global_admin = _legacy('_is_global_admin_requester', namespace)
        namespace['_is_global_admin_requester'] = global_admin
        return namespace

    from employees.routes import build_employees_blueprint

    app = Flask(__name__)

    def is_global_admin(role, requester_id=None):
        return legacy_namespace()['_is_global_admin_requester'](role, requester_id)

    app.register_blueprint(build_employees_blueprint(
        db=_DB(cur),
        require_api_key=lambda f: f,
        get_authenticated_requester=lambda: (viewer().id, (viewer().id, None, 'x', viewer().role), None),
        normalize_role=helpers['_normalize_user_role'],
        is_admin_role=helpers['_is_admin_role'],
        is_super_admin_role=helpers['_is_super_admin_role'],
        is_global_admin_requester=is_global_admin,
        headed_department_ids=lambda _id: viewer().headed,
        department_scope_id_for_requester=lambda _id: viewer().scope_dept,
        is_employee_accounting_manager=lambda _id: viewer().hr,
        build_avatar_signed_url=_avatar,
        back_office_roles=BACK_OFFICE,
        proxy_status_labels=PROXY_LABELS,
    ))
    client = app.test_client()

    class Stand:
        def use(self, v):
            holder['viewer'] = v

        def legacy_users(self):
            payload, status = _legacy('get_admin_users', legacy_namespace())()
            assert status == 200, payload
            return payload['users']

        def legacy_supervisors(self):
            result = _legacy('get_sv_list', legacy_namespace())()
            payload, status = result if isinstance(result, tuple) else (result, 200)
            return payload, status

        def page(self, **params):
            response = client.get('/api/admin/employees/page?' + urlencode(params))
            return response.status_code, response.get_json()

        def all_pages(self, limit=7, **params):
            """Все страницы подряд: (строки, последний ответ)."""
            rows, offset, seen_total = [], 0, None
            while True:
                status, body = self.page(limit=limit, offset=offset, **params)
                assert status == 200, body
                if seen_total is None:
                    seen_total = body['total']
                assert body['total'] == seen_total
                assert body['offset'] == offset
                rows.extend(body['employees'])
                offset += limit
                if offset >= body['total']:
                    return rows, body

    yield Stand()
    conn.rollback()
    conn.close()


# ─────────────────────── правила браузера (App.jsx) ───────────────────────

def _status_code(status):
    value = str(status or '').strip().lower()
    if not value:
        return 'working'
    return 'bs' if value == 'unpaid_leave' else value


def _visible_by_tab(status, tab):
    key = _status_code(status)
    key = 'fired' if key == 'dismissal' else key
    if tab == 'active':
        return key != 'fired'
    if tab == 'fired':
        return key == 'fired'
    return key == tab


def _direction_label(row):
    return row.get('direction') or 'Без направления'


def _matches(row, query, direction_label=None):
    q = query.strip().lower()
    if not q:
        return True
    direction = direction_label if direction_label is not None else (row.get('direction') or '')
    return any(q in str(value or '').lower()
               for value in (row.get('name'), direction, row.get('job_title'), row.get('supervisor_name')))


_PAST, _PAST_2 = chr(0xFFFC), chr(0xFFFD)
_LETTERS = {'ё': 'е', 'ә': 'а' + _PAST, 'ғ': 'г' + _PAST, 'і': 'и' + _PAST, 'қ': 'к' + _PAST, 'ң': 'н' + _PAST,
            'ө': 'о' + _PAST, 'ү': 'у' + _PAST, 'ұ': 'у' + _PAST_2, 'һ': 'х' + _PAST}
# Латиница у браузерного 'ru' — после кириллицы в любом месте строки.
_LETTERS.update({chr(ord('a') + index): chr(0x0500 + index) for index in range(26)})


def _text_key(value):
    return ''.join(_LETTERS.get(ch, ch) for ch in str(value or '').lower())


_STATUS_RANK = {'working': 1, 'bs': 2, 'sick_leave': 3, 'annual_leave': 4, 'dismissal': 5, 'fired': 6}


def _hire(row):
    raw = row.get('hire_date')
    if not raw:
        return date(1970, 1, 1)
    d, m, y = raw.split('-')
    return date(int(y), int(m), int(d))


def _primary(row, sort, supervisors=False):
    if supervisors and sort in ('direction', 'supervisor'):
        return None
    if sort == 'direction':
        return _text_key(row.get('direction'))
    if sort == 'supervisor':
        return _text_key(row.get('supervisor_name'))
    if sort == 'status':
        return _STATUS_RANK.get(_status_code(row.get('status')), 7)
    if sort == 'hire_date':
        return _hire(row)
    if sort == 'rate':
        return float(row.get('rate') or 0)
    return None


def _cmp(a, b):
    return (a > b) - (a < b)


def _browser_order(rows, sort, direction, supervisors=False):
    """compareUsersByField: поле в выбранную сторону, при равенстве имя от А к Я, потом id."""
    sign = -1 if direction == 'desc' else 1

    def compare(x, y):
        if sort == 'name':
            result = _cmp(_text_key(x['name']), _text_key(y['name'])) * sign
        else:
            px, py = _primary(x, sort, supervisors), _primary(y, sort, supervisors)
            result = 0 if px is None else _cmp(px, py) * sign
            if result == 0:
                result = _cmp(_text_key(x['name']), _text_key(y['name']))
        return result or _cmp(x['id'], y['id'])

    return sorted(rows, key=functools.cmp_to_key(compare))


def _browser_operator_groups(rows, me, sort, direction):
    """Таблица «Операторов»: свои, потом направления; «по направлению» переставляет группы."""
    sign = -1 if direction == 'desc' else 1
    mine = [row for row in rows if row.get('supervisor_id') == me]
    others = {}
    for row in rows:
        if row.get('supervisor_id') != me:
            others.setdefault(_direction_label(row), []).append(row)
    if sort == 'direction':
        by_name = functools.cmp_to_key(
            lambda x, y: (_cmp(_text_key(x['name']), _text_key(y['name'])) * sign) or _cmp(x['id'], y['id']))
        order = lambda group: sorted(group, key=by_name)  # noqa: E731
        labels = sorted(others, key=functools.cmp_to_key(lambda a, b: _cmp((_text_key(a), a), (_text_key(b), b)) * sign))
    else:
        order = lambda group: _browser_order(group, sort, direction)  # noqa: E731
        labels = sorted(others, key=lambda label: (_text_key(label), label))
    groups = []
    if mine:
        groups.append(('mine', order(mine)))
    groups.extend((f'dir:{label}', order(others[label])) for label in labels)
    return groups


def _client_lists(v, legacy_rows):
    """Списки раздела из полного ответа — как App.jsx (fetchUsers и operatorUsers)."""
    norm = _monolith_namespace()['_normalize_user_role']
    department_manager = norm(v.role) == 'sv' or (bool(v.headed) and v.role != 'super_admin')
    manage = ({'operator', 'trainee', 'sv', 'supervisor', *BACK_OFFICE} if (department_manager or v.hr)
              else {'operator', *BACK_OFFICE})
    users = [row for row in legacy_rows if norm(row['role']) in manage]
    return {
        'staff': (sorted(manage & RANK_AND_FILE), [row for row in users if norm(row['role']) in RANK_AND_FILE]),
        'trainers': (['trainer'], [row for row in legacy_rows if str(row['role']).lower() == 'trainer']),
        'admins': (['admin'], [row for row in legacy_rows if str(row['role']).lower() == 'admin']),
        'operators': (sorted(manage), users),
    }


def _ids(rows):
    return [row['id'] for row in rows]


def _without_group(row):
    return {key: value for key, value in row.items() if key != 'list_group'}


# ─────────────────────────────── проверки ───────────────────────────────

def test_name_order_matches_the_browser(stand):
    stand.use(VIEWER['super_admin'])
    rows, _ = stand.all_pages(list='users', roles=','.join(sorted(RANK_AND_FILE)), status='active', limit=5)
    active_names = [name for name in BROWSER_NAME_ORDER
                    if name not in ('Ахметов Бауыржан', 'Ахметова Айгерим', 'Оператор ОП Два', 'Стажёр Ольга')]
    assert [row['name'] for row in rows] == active_names


@pytest.mark.parametrize('viewer', VIEWERS, ids=lambda v: v.key)
def test_every_list_shows_the_same_people_with_the_same_fields(stand, viewer):
    stand.use(viewer)
    legacy = stand.legacy_users()
    by_id = {row['id']: row for row in legacy}
    for name, (roles, expected) in _client_lists(viewer, legacy).items():
        for tab in ('active', 'fired'):
            rows, body = stand.all_pages(list='users', roles=','.join(roles), status=tab)
            want = [row for row in expected if _visible_by_tab(row['status'], tab)]
            assert sorted(_ids(rows)) == sorted(_ids(want)), (name, tab)
            assert len(set(_ids(rows))) == len(rows), (name, tab, 'страницы повторяют людей')
            for row in rows:
                assert row == by_id[row['id']], (name, row['id'])
            assert body['list_total'] == len(expected), name


@pytest.mark.parametrize('viewer', VIEWERS, ids=lambda v: v.key)
def test_status_counts_department_filter_and_search(stand, viewer):
    stand.use(viewer)
    legacy = stand.legacy_users()
    for name, (roles, expected) in _client_lists(viewer, legacy).items():
        for department in (None, 1, 2):
            rows_in_dept = [row for row in expected
                            if department is None or row.get('department_id') == department]
            params = {'list': 'users', 'roles': ','.join(roles)}
            if department is not None:
                params['department'] = department
            for tab in STATUS_TABS:
                status, body = stand.page(status=tab, limit=200, **params)
                assert status == 200, body
                want = [row for row in rows_in_dept if _visible_by_tab(row['status'], tab)]
                assert body['total'] == len(want), (name, department, tab)
                assert body['status_counts'] == {
                    key: sum(1 for row in rows_in_dept if _visible_by_tab(row['status'], key)) for key in STATUS_TABS
                }, (name, department)
            for query in ('иван', 'ИВАН', 'доставка', 'сабина', 'бухгалтер', 'таргетолог', ' ким ', 'zzz', 'Ақтөбе'):
                status, body = stand.page(status='active', q=query, limit=200, **params)
                want = [row for row in rows_in_dept
                        if _visible_by_tab(row['status'], 'active') and _matches(row, query)]
                assert sorted(_ids(body['employees'])) == sorted(_ids(want)), (name, department, query)


@pytest.mark.parametrize('sort', ['name', 'direction', 'supervisor', 'status', 'hire_date', 'rate'])
@pytest.mark.parametrize('direction', ['asc', 'desc'])
def test_order_is_the_browser_order_across_pages(stand, sort, direction):
    for viewer in (VIEWER['super_admin'], VIEWER['sv_dept1'], VIEWER['hr']):
        stand.use(viewer)
        roles, expected = _client_lists(viewer, stand.legacy_users())['staff']
        for tab in ('active', 'fired'):
            rows, _ = stand.all_pages(list='users', roles=','.join(roles), status=tab, sort=sort, dir=direction,
                                      limit=4)
            want = _browser_order([row for row in expected if _visible_by_tab(row['status'], tab)], sort, direction)
            assert _ids(rows) == _ids(want), (viewer.key, tab, sort, direction)


@pytest.mark.parametrize('sort', ['name', 'direction', 'status', 'hire_date', 'rate', 'supervisor'])
@pytest.mark.parametrize('direction', ['asc', 'desc'])
def test_operators_are_grouped_mine_first_then_directions(stand, sort, direction):
    for viewer in (VIEWER['sv_dept1'], VIEWER['trainer']):
        stand.use(viewer)
        roles, expected = _client_lists(viewer, stand.legacy_users())['operators']
        for query in ('', 'без направления', 'иван'):
            rows, body = stand.all_pages(list='users', roles=','.join(roles), group='operators', status='active',
                                         sort=sort, dir=direction, q=query, limit=4)
            matched = [row for row in expected if _visible_by_tab(row['status'], 'active')
                       and _matches(row, query, _direction_label(row))]
            groups = _browser_operator_groups(matched, viewer.id, sort, direction)
            assert _ids(rows) == [row['id'] for _, group in groups for row in group], (viewer.key, sort, query)
            assert [row['list_group'] for row in rows] == [key for key, group in groups for _ in group]
            assert [(group['key'], group['title'], group['total']) for group in body['groups']] == \
                [(key, 'Мои операторы' if key == 'mine' else key[len('dir:'):], len(group)) for key, group in groups], \
                (viewer.key, sort, query)


def test_supervisors_list_mirrors_the_full_profile(stand):
    for viewer in VIEWERS:
        stand.use(viewer)
        legacy, legacy_status = stand.legacy_supervisors()
        status, body = stand.page(list='supervisors', status='active', limit=200)
        # Список раздела — у тех, кому полная ручка отдаёт полный профиль: админ,
        # глава отдела, кадровик. СВ полная ручка отдаёт урезанную проекцию для
        # других разделов, тренеру — 403.
        if not (viewer.role in ('admin', 'super_admin') or viewer.headed or viewer.hr):
            assert status == 403, (viewer.key, body)
            continue
        assert legacy_status == 200 and all('phone' in row for row in legacy['sv_list']), viewer.key
        assert status == 200, (viewer.key, body)
        by_id = {row['id']: row for row in legacy['sv_list']}
        rows, _ = stand.all_pages(list='supervisors', status='active', limit=2)
        want = [row for row in legacy['sv_list'] if _visible_by_tab(row['status'], 'active')]
        assert _ids(rows) == _ids(_browser_order(want, 'name', 'asc')), viewer.key
        for row in rows:
            assert row == by_id[row['id']], (viewer.key, row['id'])
        for sort in ('direction', 'supervisor'):
            rows, _ = stand.all_pages(list='supervisors', status='active', sort=sort, dir='desc', limit=2)
            assert _ids(rows) == _ids(_browser_order(want, sort, 'desc', supervisors=True)), (viewer.key, sort)
        assert body['status_counts']['fired'] == sum(
            1 for row in legacy['sv_list'] if _visible_by_tab(row['status'], 'fired'))


def test_closed_to_people_without_the_section(stand):
    for viewer in (Viewer('operator', 30, 'operator', scope_dept=1),
                   Viewer('accountant', 21, 'accounting_manager', scope_dept=4)):
        stand.use(viewer)
        assert stand.page(list='users')[0] == 403
        assert stand.page(list='supervisors')[0] == 403
    stand.use(Viewer('sv_dept1', 1, 'sv', scope_dept=1))
    assert stand.page(list='supervisors')[0] == 403


def test_page_past_the_end_falls_back_to_the_last_one(stand):
    stand.use(VIEWER['super_admin'])
    roles = ','.join(sorted(RANK_AND_FILE))
    status, first = stand.page(list='users', roles=roles, limit=10)
    total = first['total']
    status, body = stand.page(list='users', roles=roles, limit=10, offset=500)
    assert status == 200
    assert body['offset'] == ((total - 1) // 10) * 10
    assert len(body['employees']) == total - body['offset']
    status, body = stand.page(list='users', roles=roles, limit=10, offset=13)
    assert body['offset'] == 10


def test_ids_are_every_match_in_page_order(stand):
    stand.use(VIEWER['super_admin'])
    roles = ','.join(sorted(RANK_AND_FILE))
    rows, _ = stand.all_pages(list='users', roles=roles, status='active', sort='hire_date', dir='desc', limit=6)
    status, body = stand.page(list='users', roles=roles, status='active', sort='hire_date', dir='desc', limit=6,
                              ids=1)
    assert body['ids'] == _ids(rows)


def test_bad_parameters_are_400(stand):
    stand.use(VIEWER['super_admin'])
    for params in ({'status': 'vacation'}, {'sort': 'salary'}, {'dir': 'up'}, {'limit': 'x'}, {'offset': -1},
                   {'department': 'abc'}, {'list': 'everyone'}, {'list': 'supervisors', 'group': 'operators'}):
        status, body = stand.page(**params)
        assert status == 400, (params, body)


def test_nul_in_search_is_ignored_not_500(stand):
    stand.use(VIEWER['super_admin'])
    roles = ','.join(sorted(RANK_AND_FILE))
    status, plain = stand.page(list='users', roles=roles, q='иван', limit=200)
    status_nul, with_nul = stand.page(list='users', roles=roles, q='ив\x00ан', limit=200)
    assert (status, status_nul) == (200, 200), with_nul
    assert _ids(with_nul['employees']) == _ids(plain['employees'])
