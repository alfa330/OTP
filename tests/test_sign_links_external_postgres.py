# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: SIGN_LINKS_TEST_PORT — порт локального Postgres.

Внешний вход раздела «Ссылка на подписание» (sign_links/external.py) пишет в
журнал пачку одним INSERT из массивов, считает дневной предел по журналу и
отделяет свои строки от запросов сотрудников. tests/test_sign_links_external.py
сверяет правила на курсоре-двойнике и видит только, что запрос НАПИСАН. Что он
ЗНАЧИТ — ложатся ли массивы в строки, чьи строки идут в предел, кто видит их в
журнале — исполняется здесь, на настоящей схеме раздела (sign_links.schema).

Ни боевых учёток, ни импорта приложения, ни генератора: только 127.0.0.1, своя
схема со случайным именем, в конце она удаляется. Запуск на локальном кластере
(пароль, если он есть, — через PGPASSWORD):

    SIGN_LINKS_TEST_PORT=55433 python -m pytest tests/test_sign_links_external_postgres.py

ИИН собраны по алгоритму, ключ выдуман.
"""
import contextlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from sign_links import api_key, iin, queries, schema
from sign_links.external import build_sign_links_external_blueprint

PORT = os.environ.get('SIGN_LINKS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (SIGN_LINKS_TEST_PORT)')

URL = '/api/sign_links/external/links'
KEY = 'FAKE-key-for-tests-0123456789-abcdefghij'
MARK = api_key.fingerprint(KEY)
OPERATOR = {'user_id': 10, 'name': 'Сзовов Саян', 'role': 'operator', 'department_id': 1,
            'department_code': 'szov', 'headed_department_ids': [], 'headed_department_codes': []}

NO_DOCS = {'outcome': 'no_documents', 'link': None, 'error': None, 'latency_ms': 300,
           'message': 'Нет документов на подписание. Документы подписаны'}
DOWN = {'outcome': 'unavailable', 'link': None, 'message': None, 'latency_ms': 10000,
        'error': 'форма не открылась: timeout'}


def synthetic_iins(count):
    found, serial = [], 0
    while len(found) < count:
        head = '9001013%04d' % serial
        serial += 1
        control = iin._checksum(head)
        if control is not None:
            found.append(head + str(control))
    return found


A, B, C, D, E, F = synthetic_iins(6)


class _Db:
    """То, что роут ждёт от Database: _get_cursor с коммитом на выходе."""

    def __init__(self, name):
        self.name = name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres',
                                dbname='postgres', connect_timeout=3)
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
def db():
    name = 't_sign_links_api_' + uuid.uuid4().hex
    base = _Db(name)
    admin = base.connect()
    try:
        with admin.cursor() as cur:
            cur.execute('CREATE SCHEMA ' + name)
            cur.execute('SET search_path TO ' + name)
            cur.execute('CREATE TABLE users (id integer PRIMARY KEY, name text)')
            cur.execute("INSERT INTO users (id, name) VALUES (10, 'Сзовов Саян')")
            schema.init_sign_links_schema(cur)
        admin.commit()
    except Exception:
        admin.rollback()
        admin.close()
        raise
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + name + ' CASCADE')
        admin.commit()
        admin.close()


@pytest.fixture
def client(db):
    from flask import Flask

    plan = {}

    def generate(value):
        answer = plan.get(value)
        return dict(answer) if answer else {
            'outcome': 'link', 'link': 'https://sign.example.kz/s/' + value,
            'message': None, 'error': None, 'latency_ms': 700}

    def build(daily_limit=None):
        app = Flask(__name__)
        app.register_blueprint(build_sign_links_external_blueprint(
            db=db, client_ip=lambda: '203.0.113.7', generate=generate, executor=executor,
            environ={api_key.ENV_NAME: KEY}, daily_limit=daily_limit))
        return app.test_client()

    executor = ThreadPoolExecutor(max_workers=4)
    try:
        yield build, plan
    finally:
        executor.shutdown(wait=True)


def post(http, iins):
    return http.post(URL, json={'iins': iins},
                     headers={api_key.HEADER: KEY, 'User-Agent': 'partner-bot/2.1'})


def rows(db, sql, params=None):
    with db._get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def staff_request(db, value, outcome='link'):
    with db._get_cursor() as cur:
        queries.log_request(cur, OPERATOR, iin=value, outcome=outcome,
                            link_issued=outcome == 'link', ip_address='10.0.0.7')


def test_a_batch_becomes_rows_of_the_external_service(db, client):
    build, plan = client
    plan.update({B: NO_DOCS, C: DOWN})
    response = post(build(), [A, B, C, '12345', A])
    assert response.status_code == 200
    assert [item['status'] for item in response.get_json()['results']] == [
        'link', 'not_found', 'error', 'invalid', 'link']

    logged = rows(db, '''SELECT iin, outcome, link_issued, link_host, vendor_message, error_text,
                                latency_ms, user_id, user_name, user_role, department_id,
                                department_code, ip_address, user_agent, created_at IS NOT NULL
                           FROM sign_link_requests ORDER BY id''')
    # Повтор ИИН — одна строка; порядок — как прислали, опечатки в конце.
    assert [row[:3] for row in logged] == [
        (A, 'link', True), (B, 'no_documents', False), (C, 'unavailable', False),
        ('12345', 'invalid', False)]
    assert [row[3] for row in logged] == ['sign.example.kz', None, None, None]
    assert logged[1][4] == NO_DOCS['message']
    assert [row[5] for row in logged] == [None, None, DOWN['error'], 'length']
    assert [row[6] for row in logged] == [700, 300, 10000, None]
    for row in logged:
        # Кто: не сотрудник и не отдел, а внешний сервис с отпечатком ключа.
        assert row[7:] == (None, 'Внешний сервис (ключ %s)' % MARK, 'api', None, None,
                           '203.0.113.7', 'partner-bot/2.1', True)

    # Ни ссылки, ни ключа в базе нет — ни в одной колонке.
    dump = repr(rows(db, 'SELECT r::text FROM sign_link_requests r'))
    assert 'sign.example.kz/s/' not in dump
    assert KEY not in dump


def test_a_batch_of_plain_links_fits_arrays_of_nulls(db, client):
    """Все сообщения и ошибки пусты — массивы из одних NULL обязаны разобраться."""
    build, _plan = client
    assert post(build(), [A, B, C]).status_code == 200
    assert rows(db, '''SELECT count(*), count(vendor_message), count(error_text),
                              count(*) FILTER (WHERE link_issued)
                         FROM sign_link_requests''') == [(3, 0, 0, 3)]


def test_garbage_from_foreign_json_does_not_sink_the_batch(db, client):
    """NUL в строке Postgres не принимает вовсе — и одна такая опечатка уронила
    бы запись всей пачки вместе с выданной ссылкой."""
    build, _plan = client
    body = '{"iins": ["%s", "12\\u000034", "\\ud800", "ab\\u0007cd"]}' % A
    response = build().post(URL, data=body, headers={api_key.HEADER: KEY})
    assert response.status_code == 200
    assert [item['status'] for item in response.get_json()['results']] == [
        'link', 'invalid', 'invalid', 'invalid']
    assert rows(db, 'SELECT iin, outcome, error_text FROM sign_link_requests ORDER BY id') == [
        (A, 'link', None), ('1234', 'invalid', 'length'), ('abcd', 'invalid', 'digits')]


def test_the_daily_limit_counts_todays_rows_of_the_service_only(db, client):
    build, _plan = client
    http = build(daily_limit=5)
    staff_request(db, A)
    staff_request(db, B)
    assert post(http, [A, B, '12345']).get_json()['limits'] == {
        'per_day': 5, 'used_today': 3, 'left_today': 2}
    with db._get_cursor() as cur:
        # Запросы сотрудников в предел сервиса не идут, опечатка сервиса — идёт.
        assert queries.api_used_today(cur) == 3
        assert queries.used_today(cur, OPERATOR['user_id']) == 2

    # Трое не помещаются — отказ целиком, в базе ничего не прибавилось.
    refused = post(http, [C, D, E])
    assert refused.status_code == 429
    assert refused.get_json()['limits'] == {'per_day': 5, 'used_today': 3, 'left_today': 2}
    assert rows(db, "SELECT count(*) FROM sign_link_requests WHERE user_role = 'api'") == [(3,)]
    assert post(http, [C, D]).status_code == 200
    assert post(http, [E]).status_code == 429

    # Вчерашнее в сегодняшний предел не идёт.
    with db._get_cursor() as cur:
        cur.execute("UPDATE sign_link_requests SET created_at = created_at - interval '2 days' "
                    "WHERE user_role = 'api'")
    assert post(http, [E]).status_code == 200
    with db._get_cursor() as cur:
        assert queries.api_used_today(cur) == 1


def test_the_journal_separates_the_service_from_the_staff(db, client):
    build, plan = client
    plan[B] = NO_DOCS
    with db._get_cursor() as cur:
        assert queries.journal_has_api_rows(cur) is False
    staff_request(db, A)
    staff_request(db, F, outcome='no_documents')
    assert post(build(), [A, B, C]).status_code == 200

    def journal(**filters):
        with db._get_cursor() as cur:
            page = queries.journal_page(cur, dict({'scope': None}, **filters))
        return sorted((item['iin'], item['user_role']) for item in page['items']), page['summary']

    everything, summary = journal()
    assert everything == sorted([(A, 'operator'), (F, 'operator'), (A, 'api'), (B, 'api'),
                                 (C, 'api')])
    assert (summary['requests'], summary['links'], summary['no_documents']) == (5, 3, 2)
    # Людей — один: у строк сервиса сотрудника нет.
    assert summary['people'] == 1

    only_api, summary = journal(source='api')
    assert only_api == sorted([(A, 'api'), (B, 'api'), (C, 'api')])
    assert (summary['requests'], summary['links']) == (3, 2)

    only_staff, summary = journal(source='staff')
    assert only_staff == sorted([(A, 'operator'), (F, 'operator')])
    assert (summary['requests'], summary['links']) == (2, 1)

    # «Кто запрашивал этого водителя» находит и сотрудника, и сервис.
    assert journal(iin=A)[0] == sorted([(A, 'operator'), (A, 'api')])

    # Глава отдела строк сервиса не видит — ни целиком, ни отбором.
    assert journal(scope=['szov'])[0] == sorted([(A, 'operator'), (F, 'operator')])
    assert journal(scope=['szov'], source='api')[0] == []

    with db._get_cursor() as cur:
        assert queries.journal_has_api_rows(cur) is True
        # В выпадающем списке сотрудников сервиса нет: это не сотрудник.
        assert [person['user_id'] for person in queries.journal_people(cur)] == [10]


def test_a_deleted_account_is_not_mistaken_for_the_service(db, client):
    """У строки уволенного user_id тоже пуст (ON DELETE SET NULL) — отличает роль."""
    build, _plan = client
    staff_request(db, A)
    with db._get_cursor() as cur:
        cur.execute('DELETE FROM users WHERE id = 10')
    assert rows(db, 'SELECT user_id, user_role FROM sign_link_requests') == [(None, 'operator')]
    with db._get_cursor() as cur:
        assert queries.api_used_today(cur) == 0
        assert queries.journal_has_api_rows(cur) is False
        page = queries.journal_page(cur, {'scope': None, 'source': 'staff'})
    assert [item['iin'] for item in page['items']] == [A]


def test_the_limit_query_walks_an_index_not_the_whole_journal(db):
    """Предел считается на каждый запрос сервиса: за месяцы журнал вырастет, а
    читать надо только сегодняшнее."""
    with db._get_cursor() as cur:
        cur.execute('''INSERT INTO sign_link_requests (user_id, user_role, iin, outcome, created_at)
                       SELECT NULL, 'api', g::text, 'no_documents',
                              now() - interval '2 days' - (g || ' minutes')::interval
                         FROM generate_series(1, 30000) g''')
        cur.execute('''INSERT INTO sign_link_requests (user_id, user_role, iin, outcome, created_at)
                       SELECT 10, 'operator', g::text, 'link',
                              now() - (g || ' minutes')::interval
                         FROM generate_series(1, 5000) g''')
        cur.execute('''INSERT INTO sign_link_requests (user_id, user_role, iin, outcome)
                       SELECT NULL, 'api', g::text, 'no_documents' FROM generate_series(1, 300) g''')
        cur.execute('ANALYZE sign_link_requests')
        cur.execute('EXPLAIN ' + queries._API_USED_TODAY_SQL,
                    {'api_role': queries.API_ROLE, 'day_start': queries.today_start_almaty()})
        plan = '\n'.join(row[0] for row in cur.fetchall())
        assert queries.api_used_today(cur) == 300
    assert 'Seq Scan' not in plan, plan
    assert 'idx_sign_link_requests_' in plan, plan
