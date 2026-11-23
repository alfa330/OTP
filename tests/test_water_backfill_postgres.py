# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: WATER_TEST_PORT — порт локального Postgres.

Приветственный блок задним числом (владелец 23.11.2026: «супер-админам
добавлять такие блоки задним числом, в случае если офисник забыл нажать
кнопку, а водитель уже выполняет заказы»). Здесь исполняется то, что в
tests/test_water_routes.py подменено: как выдача вписывается в историю офиса —
«остаток после» более поздних строк, пересчёт, который уже насчитал полку без
этого блока, отменённые выдачи, нехватка воды на тот момент. Каждый сценарий
кончается проигрышем истории офиса с нуля: все «остатки после» и остаток офиса
сходятся до блока.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со
случайным именем, в конце она удаляется. Запуск (пароль — через PGPASSWORD):

    WATER_TEST_PORT=55433 PGPASSWORD=verify python -m pytest tests/test_water_backfill_postgres.py

Люди выдуманы; id офисника — из списка water.access.ISSUER_USER_IDS.
"""
import contextlib
import json
import os
import uuid
from datetime import datetime

import pytest

from water import queries, rules, schema

PORT = os.environ.get('WATER_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (WATER_TEST_PORT)')

ISSUER, SUPER = 425, 1
ACCOUNT = 'k' * 32
IIN = '900101300555'
ACTOR = {'user_id': SUPER, 'name': 'Суперов Самат'}
NOW = datetime(2026, 11, 23, 11, 0)
MOMENT = datetime(2026, 11, 22, 12, 0)

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL,
                        status text NOT NULL DEFAULT 'working', department_id integer, city text,
                        telegram_id bigint);
    CREATE TABLE wiki_offices (id integer PRIMARY KEY);
    INSERT INTO departments (id, code, name) VALUES (909, 'front_office', 'Фронт офисы'), (1, 'szov', 'СЗоВ');
    INSERT INTO users (id, name, role, status, department_id, city) VALUES
        (425, 'Астанов Асет', 'operator', 'working', 909, 'Астана'),
        (1, 'Суперов Самат', 'super_admin', 'working', 1, NULL);
    INSERT INTO wiki_offices (id) VALUES (49);
'''


class _Db:
    """Тот же контракт, что Database._get_cursor: коммит по выходу, откат по исключению."""

    def __init__(self, name):
        self.name = name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                                connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.name)
            cur.execute("SET statement_timeout TO '10s'")
        conn.commit()
        return conn

    @contextlib.contextmanager
    def _get_cursor(self):
        conn = self.connect()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()


@pytest.fixture
def db(monkeypatch):
    name = 't_water_backfill_' + uuid.uuid4().hex
    base = _Db(name)
    admin = base.connect()
    try:
        with admin.cursor() as cur:
            cur.execute('CREATE SCHEMA ' + name)
            cur.execute('SET search_path TO ' + name)
            cur.execute(BASE_DDL)
            schema.init_water_schema(cur)
        admin.commit()
    except Exception:
        admin.rollback()
        admin.close()
        raise
    monkeypatch.setattr(queries, 'access_ready', lambda cursor: True)
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + name + ' CASCADE')
        admin.commit()
        admin.close()


def rows(db, sql, params=None):
    with db._get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def office(db, events, opened='2026-11-01 10:00', wiki_id=49):
    """Офис Астаны с историей. events — по порядку: ('recount', время, сколько
    насчитали), ('intake', время, сколько), ('issue', время, блоков[, отменена в]).
    Второй офис в одном тесте — wiki_id=None: офис вики в учёте один раз."""
    with db._get_cursor() as cur:
        cur.execute("INSERT INTO water_offices (office_id, city, name, stock, created_at) "
                    "VALUES (%s, 'Астана', 'Офис Астана', 0, %s) RETURNING id", (wiki_id, opened))
        office_id = cur.fetchone()[0]
        cur.execute('INSERT INTO water_issuer_offices (user_id, water_office_id) VALUES (%s, %s)',
                    (ISSUER, office_id))
        stock = 0
        for event in events:
            kind, at = event[0], event[1]
            if kind == 'recount':
                cur.execute("INSERT INTO water_movements (water_office_id, kind, delta, stock_after, "
                            "created_at) VALUES (%s, 'recount', %s, %s, %s)",
                            (office_id, event[2] - stock, event[2], at))
                stock = event[2]
            elif kind == 'intake':
                stock += event[2]
                cur.execute("INSERT INTO water_movements (water_office_id, kind, delta, stock_after, "
                            "created_at) VALUES (%s, 'intake', %s, %s, %s)", (office_id, event[2], stock, at))
            else:
                stock -= event[2]
                canceled = event[3] if len(event) > 3 else None
                cur.execute("INSERT INTO water_issues (water_office_id, city, office_name, kind, blocks, "
                            "stock_after, driver_account_id, issued_by, issued_by_name, created_at, "
                            "canceled_at) VALUES (%s, 'Астана', 'Офис Астана', 'activity', %s, %s, %s, %s, "
                            "'Астанов Асет', %s, %s)",
                            (office_id, event[2], stock, uuid.uuid4().hex, ISSUER, at, canceled))
        # Отмена возвращает блоки на полку в свою минуту: остаток офиса — с ними.
        cur.execute('SELECT COALESCE(SUM(blocks), 0) FROM water_issues '
                    'WHERE water_office_id = %s AND canceled_at IS NOT NULL', (office_id,))
        stock += cur.fetchone()[0]
        cur.execute('UPDATE water_offices SET stock = %s WHERE id = %s', (stock, office_id))
    return office_id


def replay(db, office_id):
    """Проигрыш истории офиса с нуля → (расхождения, итог, остаток офиса)."""
    events = rows(db, """
        SELECT created_at, 0 AS src, id, 'issue', blocks, stock_after FROM water_issues WHERE water_office_id = %(o)s
        UNION ALL
        SELECT canceled_at, 2, id, 'cancel', blocks, NULL FROM water_issues
         WHERE water_office_id = %(o)s AND canceled_at IS NOT NULL
        UNION ALL
        SELECT created_at, 1, id, kind, delta, stock_after FROM water_movements WHERE water_office_id = %(o)s
        ORDER BY 1, 2, 3""", {'o': office_id})
    stock, bad = 0, []
    for at, _src, row_id, kind, amount, after in events:
        if kind == 'issue':
            stock -= amount
        elif kind in ('cancel', 'intake'):
            stock += amount
        else:  # пересчёт: разница записана — полка = до + разница
            stock += amount
        if after is not None and after != stock:
            bad.append((kind, row_id, str(at), after, stock))
            stock = after
    return bad, stock, rows(db, 'SELECT stock FROM water_offices WHERE id = %s', (office_id,))[0][0]


def crm(times=('2026-11-22 10:03:05', '2026-11-22 10:29:30', '2026-11-22 10:49:16',
               '2026-11-23 07:31:14', '2026-11-23 07:54:02')):
    items = [{'booked_at': moment, 'category': 'business'} for moment in times]
    return {
        'account_id': ACCOUNT, 'iin': IIN, 'name': 'Водителев Вадим', 'phone': '+77000000555',
        'park': 'Парк', 'park_id': 'p' * 32, 'car': 'L7 · 001AAA01', 'tariffs': ['business', 'econom'],
        'work_status': 'working', 'is_blocked': False, 'fired': False, 'registered_at': '2026-11-21 10:29:05',
        'photo_control': {'status': None, 'checked_at': None},
        'orders': {'today': 2, 'week': len(times), 'month': len(times), 'total': len(times),
                   'window_from': '2026-11-16 11:00:00', 'week_truncated': False},
        'order_times': list(times),
        'info': {'driver': {'iin': IIN}, 'orders': {
            'today': 2, 'week': len(times), 'total': len(times),
            'last_7_days': {'from': '2026-11-16 11:00:00', 'to': '2026-11-23 11:00:00',
                            'count': len(items), 'items': items}}},
    }


@pytest.fixture
def client(db, monkeypatch):
    """Настоящие роуты и SQL раздела; подменены вход (супер-админ), CRM и «сейчас»."""
    flask = pytest.importorskip('flask')
    from water import routes
    monkeypatch.setattr(queries, 'load_access_context', lambda cursor, user_id: {
        'user_id': SUPER, 'name': 'Суперов Самат', 'role': 'super_admin', 'department_code': 'szov',
        'city': None, 'headed_department_ids': [], 'headed_department_codes': []})
    monkeypatch.setattr(queries, 'now_almaty', lambda: NOW)
    monkeypatch.setattr(queries, 'today_almaty', lambda: NOW.date())
    monkeypatch.setattr(queries, 'notify_recipients', lambda cursor, settings: [])
    # Схема теста — не public: «раздел развёрнут» отвечаем сами.
    monkeypatch.setattr(schema, 'schema_is_ready', lambda cursor: True)
    driver = {'card': crm()}
    counts = {'value': 0, 'calls': []}

    def count_orders(account_id, date_from, date_to):
        counts['calls'].append((account_id, date_from, date_to))
        return counts['value']

    app = flask.Flask(__name__)
    app.register_blueprint(routes.build_water_blueprint(
        db=db, require_api_key=lambda f: f, build_cors_preflight_response=lambda: ('', 204),
        resolve_requester=lambda: (SUPER, None, None), sensitive_access_granted=lambda _uid: True,
        driver_lookup=lambda raw: json.loads(json.dumps(driver['card'])),
        orders_counter=count_orders))
    app.config['TESTING'] = True
    test_client = app.test_client()
    test_client.driver = driver
    test_client.counts = counts
    return test_client


def backfill(client, office_id, issued_at='2026-11-22T12:00', **body):
    body = dict({'kind': 'welcome', 'link': ACCOUNT, 'water_office_id': office_id, 'issued_at': issued_at,
                 'issued_by': ISSUER, 'fk_confirmed': True}, **body)
    return client.post('/api/water/backfill', json=body)


HISTORY = [('recount', '2026-11-01 10:00', 30), ('issue', '2026-11-20 09:00', 1),
           ('issue', '2026-11-22 09:02', 1), ('issue', '2026-11-22 15:49', 1),
           ('intake', '2026-11-22 16:00', 10), ('issue', '2026-11-23 08:00', 1)]


def test_backfill_shifts_later_rows_and_the_office_stock(db, client):
    office_id = office(db, HISTORY)
    assert replay(db, office_id) == ([], 36, 36)
    response = backfill(client, office_id, comment='Забыли нажать «Выдать»')
    assert response.status_code == 201, response.get_json()
    data = response.get_json()
    assert data['office']['stock'] == 35
    assert data['recount'] is None
    assert data['issue']['created_at'] == '2026-11-22T12:00:00'
    assert data['issue']['backfilled_by_name'] == 'Суперов Самат'
    assert data['issue']['backfill_comment'] == 'Забыли нажать «Выдать»'
    assert data['issue']['issued_by_name'] == 'Астанов Асет'
    assert replay(db, office_id) == ([], 35, 35)
    chain = rows(db, 'SELECT created_at, kind, stock_after FROM water_issues WHERE water_office_id = %s '
                     'ORDER BY created_at', (office_id,))
    assert [(str(at)[:16], kind, after) for at, kind, after in chain] == [
        ('2026-11-20 09:00', 'activity', 29), ('2026-11-22 09:02', 'activity', 28),
        ('2026-11-22 12:00', 'welcome', 27), ('2026-11-22 15:49', 'activity', 26),
        ('2026-11-23 08:00', 'activity', 35)]
    assert rows(db, "SELECT stock_after FROM water_movements WHERE kind = 'intake'") == [(36,)]

    # Снимок — на момент выдачи: три заказа того дня до 12:00, и следующая
    # выдача за активность вычтет именно их.
    stored = rows(db, "SELECT orders_total, orders_counted, orders_basis, fk_source, driver_iin, "
                      "driver_info -> 'orders' -> 'today', backfilled_at FROM water_issues WHERE kind = 'welcome'")
    total, counted, basis, fk, iin, today, entered = stored[0]
    assert (total, counted, basis, fk, iin, today) == (3, 0, 'new', 'confirmed', IIN, 3)
    assert entered is not None
    with db._get_cursor() as cur:
        history = queries.person_history(cur, ACCOUNT, IIN)
    assert [(item['kind'], item['orders_today']) for item in history] == [('welcome', 3)]

    # Журнал и выгрузка видят отметку.
    with db._get_cursor() as cur:
        items, _total = queries.list_issues(cur, {'kind': 'welcome'})
    assert items[0]['backfilled_by_name'] == 'Суперов Самат'
    assert items[0]['backfilled_at']

    # Второй раз тому же человеку — нет: и проверкой, и индексом базы.
    again = backfill(client, office_id, issued_at='2026-11-22T13:00')
    assert again.status_code == 409
    assert 'уже выдан 22.11.2026' in again.get_json()['error']
    assert replay(db, office_id) == ([], 35, 35)


def test_later_recount_takes_the_difference_and_keeps_the_stock(db, client):
    """Пересчёт в 18:00 насчитал 36 при 37 в системе — недостающий блок и есть
    невнесённая выдача: его «остаток после» верен, разница становится нулём,
    остаток офиса не меняется."""
    history = HISTORY[:5] + [('recount', '2026-11-22 18:00', 36), ('issue', '2026-11-23 08:00', 1)]
    office_id = office(db, history)
    assert replay(db, office_id) == ([], 35, 35)
    response = backfill(client, office_id)
    assert response.status_code == 201, response.get_json()
    assert response.get_json()['office']['stock'] == 35
    assert response.get_json()['recount']['created_at'].startswith('2026-11-22T18:00')
    assert rows(db, "SELECT delta, stock_after FROM water_movements WHERE created_at = '2026-11-22 18:00'") == [(0, 36)]
    assert rows(db, "SELECT stock_after FROM water_issues WHERE created_at = '2026-11-23 08:00'") == [(35,)]
    assert replay(db, office_id) == ([], 35, 35)


def test_canceled_issue_before_the_moment_returned_its_block(db, client):
    """Выдачу в 09:00 отменили в 11:00 — к полудню блок снова на полке: остаток
    на момент — 5, а не «остаток после» отменённой строки."""
    office_id = office(db, [('recount', '2026-11-01 10:00', 5), ('issue', '2026-11-22 09:00', 1, '2026-11-22 11:00')])
    assert replay(db, office_id) == ([], 5, 5)
    with db._get_cursor() as cur:
        plan = queries.backfill_plan(cur, office_id, MOMENT, 1)
    assert (plan['stock_before'], plan['stock_after']) == (5, 4)
    assert backfill(client, office_id).status_code == 201
    assert replay(db, office_id) == ([], 4, 4)


def test_no_water_at_the_moment_writes_nothing(db, client):
    """В полдень на полке был один блок, а в 13:00 его выдали: внести ещё один
    до 13:00 — значит, увести ту выдачу в минус."""
    office_id = office(db, [('recount', '2026-11-01 10:00', 1), ('issue', '2026-11-22 13:00', 1)])
    response = backfill(client, office_id)
    assert response.status_code == 409
    assert response.get_json()['error'] == 'На 22.11.2026 13:00 в офисе было 0 блоков — столько воды тогда не выдать'
    assert rows(db, "SELECT count(*) FROM water_issues WHERE kind = 'welcome'") == [(0,)]
    assert replay(db, office_id) == ([], 0, 0)

    empty = office(db, [('recount', '2026-11-01 10:00', 0)], wiki_id=None)
    response = backfill(client, empty)
    assert response.status_code == 409
    assert response.get_json()['error'] == 'На 22.11.2026 12:00 в офисе было 0 блоков — столько воды тогда не выдать'


def test_moment_before_the_office_opened_is_refused(db, client):
    office_id = office(db, [('recount', '2026-11-22 13:00', 10)], opened='2026-11-22 13:00')
    response = backfill(client, office_id)
    assert response.status_code == 400
    assert response.get_json()['code'] == 'WATER_MOMENT_BEFORE_OFFICE'
    assert replay(db, office_id) == ([], 10, 10)


def test_rows_at_the_very_same_minute_stay_before_the_backfilled_one(db):
    """Строка ровно в ту же минуту уже была — новая встаёт после неё."""
    office_id = office(db, [('recount', '2026-11-01 10:00', 10), ('issue', '2026-11-22 12:00', 1)])
    with db._get_cursor() as cur:
        plan = queries.backfill_plan(cur, office_id, MOMENT, 1)
    assert (plan['stock_before'], plan['issue_ids'], plan['recount']) == (9, [], None)


def test_schema_redeploy_keeps_one_foreign_key_per_column(db):
    """Колонки задним числом заводятся миграцией на каждом старте: повторный
    разворот не должен плодить внешние ключи."""
    with db._get_cursor() as cur:
        schema.init_water_schema(cur)
        schema.init_water_schema(cur)
    keys = rows(db, """SELECT a.attname, count(*) FROM pg_constraint c
                         JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
                        WHERE c.conrelid = 'water_issues'::regclass AND c.contype = 'f'
                        GROUP BY 1 ORDER BY 1""")
    assert dict(keys) == {'backfilled_by': 1, 'canceled_by': 1, 'issued_by': 1, 'water_office_id': 1}


def driver_issue(db, office_id, kind, at, account=ACCOUNT, iin=IIN, orders_today=None):
    """Выдача этому водителю в истории офиса (остаток офиса не трогаем —
    сценарии ниже о правилах водителя, а не о сдвиге). orders_today — заказы
    дня выдачи до неё в её снимке CRM."""
    info = json.dumps({'orders': {'today': orders_today}})
    with db._get_cursor() as cur:
        cur.execute("INSERT INTO water_issues (water_office_id, city, office_name, kind, blocks, stock_after, "
                    "driver_account_id, driver_iin, issued_by, issued_by_name, created_at, driver_info) "
                    "VALUES (%s, 'Астана', 'Офис Астана', %s, 1, 0, %s, %s, %s, 'Астанов Асет', %s, %s::jsonb) "
                    "RETURNING id", (office_id, kind, account, iin, ISSUER, at, info))
        return cur.fetchone()[0]


def test_activity_block_counts_days_and_trips_from_the_previous_issue(db, client):
    """Прошлая выдача человеку — 15.11 12:00, приветственная; блок за активность
    вносится на 22.11 12:00: семь дней прошло, а поездок с прошлой выдачи до этой
    по полному списку CRM три — меньше двадцати, отказ."""
    office_id = office(db, HISTORY)
    driver_issue(db, office_id, 'welcome', '2026-11-15 12:00')
    card = crm()
    card['orders']['first_order_at'] = '2026-11-22 10:03:05'   # раньше заказов не было: список полон
    client.driver['card'] = card
    check = client.post('/api/water/backfill/check', json={'link': ACCOUNT, 'issued_at': '2026-11-22T12:00'})
    activity = check.get_json()['verdicts']['activity']
    assert activity['reasons'] == ['Менее 20 поездок с прошлой выдачи до этой — было 3']
    assert (activity['trips'], activity['trips_exact'], activity['last_issue']['kind']) == (3, True, 'welcome')
    response = backfill(client, office_id, kind='activity')
    assert response.status_code == 409
    assert rows(db, "SELECT count(*) FROM water_issues WHERE kind = 'activity' AND backfilled_at IS NOT NULL") == [(0,)]

    # Порог из настроек — 3 поездки: тот же блок вносится, поездки записаны точно.
    with db._get_cursor() as cur:
        cur.execute('UPDATE water_settings SET min_trips = 3 WHERE id = 1')
    response = backfill(client, office_id, kind='activity')
    assert response.status_code == 201, response.get_json()
    assert rows(db, "SELECT kind, orders_counted, orders_basis, fk_source, driver_info -> 'orders' -> 'today' "
                    "FROM water_issues WHERE backfilled_at IS NOT NULL") == [('activity', 3, 'since_last', None, 3)]
    # Неделя не прошла — второй такой же блок на следующий день не внести.
    again = backfill(client, office_id, kind='activity', issued_at='2026-11-23T08:00')
    assert again.status_code == 409
    assert 'к этой выдаче с прошлой (22.11.2026) прошёл 1 день из 7' in again.get_json()['error']


def test_activity_trips_older_than_the_crm_list_are_counted_by_dates(db, client):
    """Прошлая выдача 10.11 12:00 (в снимке 1 заказ того дня до неё) — раньше
    списка CRM (с 16.11). Поездки с неё до 22.11 12:00 — запрос CRM за 10–22.11
    минус 1 заказ 10.11 и 0 заказов 22.11 после полудня."""
    office_id = office(db, HISTORY)
    # Прошлая выдача — в другом офисе: история этого офиса остаётся сверяемой.
    elsewhere = office(db, [('recount', '2026-11-01 10:00', 5)], wiki_id=None)
    driver_issue(db, elsewhere, 'activity', '2026-11-10 12:00', orders_today=1)
    client.counts['value'] = 1 + 5
    response = backfill(client, office_id, kind='activity')
    assert response.status_code == 409
    assert response.get_json()['error'] == 'Внести нельзя: менее 20 поездок с прошлой выдачи до этой — было 5'
    assert client.counts['calls'] == [(ACCOUNT, '2026-11-10', '2026-11-22')]

    client.counts['value'] = 1 + 24
    response = backfill(client, office_id, kind='activity')
    assert response.status_code == 201, response.get_json()
    assert rows(db, "SELECT orders_counted, orders_basis FROM water_issues WHERE backfilled_at IS NOT NULL") == [
        (24, 'since_last')]
    assert replay(db, office_id) == ([], 35, 35)


def test_the_same_request_twice_gives_one_block(db, client):
    """Разбор 23.11.2026: повтор того же запроса на ту же минуту (обрыв связи,
    вторая вкладка) — второй блок за активность не вносится, приветственный на
    этот аккаунт после него — тоже."""
    office_id = office(db, HISTORY)
    card = crm()
    card['orders']['first_order_at'] = '2026-11-22 10:03:05'
    client.driver['card'] = card
    with db._get_cursor() as cur:
        cur.execute('UPDATE water_settings SET min_trips = 3 WHERE id = 1')
    assert backfill(client, office_id, kind='activity').status_code == 201
    again = backfill(client, office_id, kind='activity')
    assert again.status_code == 409
    assert 'к этой выдаче с прошлой (22.11.2026) прошло 0 дней из 7' in again.get_json()['error']
    welcome = backfill(client, office_id)
    assert welcome.status_code == 409
    assert 'на этом аккаунте вода уже выдавалась 22.11.2026' in welcome.get_json()['error']
    assert rows(db, 'SELECT count(*) FROM water_issues WHERE backfilled_at IS NOT NULL') == [(1,)]
    assert replay(db, office_id) == ([], 35, 35)


def test_upper_bound_by_dates_refuses_a_proven_shortage(db, client):
    """Прошлая выдача — на другом аккаунте человека (по ИИН): её дня до неё не
    знаем, CRM считает целые дни 10–22.11 — «не больше». Меньше порога — отказ."""
    office_id = office(db, HISTORY)
    elsewhere = office(db, [('recount', '2026-11-01 10:00', 5)], wiki_id=None)
    driver_issue(db, elsewhere, 'activity', '2026-11-10 12:00', account='o' * 32, orders_today=4)
    client.counts['value'] = 7
    response = backfill(client, office_id, kind='activity')
    assert response.status_code == 409
    assert response.get_json()['error'] == 'Внести нельзя: менее 20 поездок с прошлой выдачи до этой — было не больше 7'
    assert client.counts['calls'] == [(ACCOUNT, '2026-11-10', '2026-11-22')]
    assert replay(db, office_id) == ([], 36, 36)


def test_backfilled_issue_before_a_recount_is_not_canceled(db, client):
    """Выдачу задним числом раньше пересчёта уже учла его разница — её отмена
    вернула бы блок на остаток второй раз (разбор 23.11.2026)."""
    history = HISTORY[:5] + [('recount', '2026-11-22 18:00', 36), ('issue', '2026-11-23 08:00', 1)]
    office_id = office(db, history)
    response = backfill(client, office_id)
    assert response.status_code == 201
    issue_id = response.get_json()['issue']['id']
    cancel = client.post('/api/water/issues/%d/cancel' % issue_id, json={'reason': 'не тот водитель'})
    assert cancel.status_code == 409
    assert cancel.get_json()['code'] == 'WATER_CANCEL_AFTER_RECOUNT'
    assert 'пересчёта 22.11.2026 18:00' in cancel.get_json()['error']
    assert rows(db, 'SELECT canceled_at FROM water_issues WHERE id = %s', (issue_id,)) == [(None,)]
    assert replay(db, office_id) == ([], 35, 35)


def test_welcome_is_refused_after_an_issue_on_the_same_account_only(db, client):
    office_id = office(db, HISTORY)
    earlier = driver_issue(db, office_id, 'activity', '2026-11-10 12:00', account='o' * 32)
    # На другом аккаунте того же человека (по ИИН) — новый парк: не помеха.
    with db._get_cursor() as cur:
        found = queries.issues_before(cur, ACCOUNT, IIN, MOMENT)
    assert found['person']['id'] == earlier and found['account'] is None
    assert backfill(client, office_id).status_code == 201

    other_office = office(db, [('recount', '2026-11-01 10:00', 5)], wiki_id=None)
    with db._get_cursor() as cur:
        cur.execute("DELETE FROM water_issues WHERE backfilled_at IS NOT NULL")
    driver_issue(db, other_office, 'activity', '2026-11-12 12:00')
    response = backfill(client, office_id)
    assert response.status_code == 409
    assert 'на этом аккаунте вода уже выдавалась 12.11.2026' in response.get_json()['error']


def test_rules_and_sql_agree_on_the_snapshot(db):
    """Снимок считается чистыми правилами — сверка с тем, что легло в базу выше."""
    card = crm()
    assert rules.orders_at(card, MOMENT) == (3, 3)
