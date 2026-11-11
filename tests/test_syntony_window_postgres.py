# -*- coding: utf-8 -*-
"""Окно Syntony на настоящем Postgres: SYNTONY_WINDOW_TEST_PORT — порт локального кластера.

Правила и ручки на подставном складе проверяет tests/test_syntony_window.py. Здесь —
настоящие запросы и схема: список доступа, поиск сотрудников, одна почта на список,
связь оператора, след выдач — и ручки поверх них с подставной Syntony.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным
именем, в конце она удаляется. Люди выдуманы.

    SYNTONY_WINDOW_TEST_PORT=55461 python -m pytest tests/test_syntony_window_postgres.py
"""

import contextlib
import os
import uuid

import pytest

from syntony_window import access, client, queries, rules, schema

PORT = os.environ.get('SYNTONY_WINDOW_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (SYNTONY_WINDOW_TEST_PORT)')

SUPER, HEAD, SV, ANNA, BORIS, FIRED, EX_HEAD = 1, 10, 20, 30, 31, 32, 40
KEY = 'sk_live_' + 'postgresTESTkey0123456789'
WINDOW_URL = 'https://app.syntony.kz/ru/embed/one-time-code'

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text UNIQUE, name text NOT NULL,
                              head_user_id integer, is_active boolean DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, login text, role text NOT NULL,
                        status text DEFAULT 'working', department_id integer, email varchar(255));
    INSERT INTO departments (id, code, name, head_user_id, is_active) VALUES
        (1, 'op', 'Отдел продаж', 10, TRUE), (2, 'old', 'Закрытый отдел', 40, FALSE);
    INSERT INTO users (id, name, login, role, status, department_id, email) VALUES
        (1, 'Супер Админов', 'super', 'super_admin', 'working', NULL, 'super@icore.kz'),
        (10, 'Глава Продаж', 'head', 'operator', 'working', 1, NULL),
        (20, 'Старший Смены', 'sv_one', 'sv', 'working', 1, 'sv@icore.kz'),
        (30, 'Анна Верификатор', 'anna', 'operator', NULL, 1, ' Anna@ICORE.kz '),
        (31, 'Борис Оператор', 'boris_100%', 'operator', 'working', 1, 'не почта'),
        (32, 'Уволенный Глеб', 'gleb', 'operator', 'fired', 1, 'gleb@icore.kz'),
        (40, 'Бывший Глава', 'exhead', 'operator', 'working', 2, NULL);
'''


class _Db:
    def __init__(self, name):
        self.name = name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT),
                                user=os.environ.get('SYNTONY_WINDOW_TEST_USER', 'postgres'),
                                dbname='postgres', connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.name)
            cur.execute("SET statement_timeout TO '20s'")
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


@pytest.fixture()
def db():
    import psycopg2
    name = 'syntony_window_' + uuid.uuid4().hex[:12]
    admin = psycopg2.connect(host='127.0.0.1', port=int(PORT),
                             user=os.environ.get('SYNTONY_WINDOW_TEST_USER', 'postgres'),
                             dbname='postgres', connect_timeout=3)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + name)
    database = _Db(name)
    try:
        with database._get_cursor() as cur:
            cur.execute(BASE_DDL)
            schema.init_syntony_window_schema(cur)
            schema.init_syntony_window_schema(cur)   # повторный старт сервера — без ошибок
        yield database
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + name + ' CASCADE')
        admin.close()


def test_context_knows_role_list_and_head_of_an_active_department(db):
    with db._get_cursor() as cur:
        queries.grant(cur, ANNA, 'anna@syntony.kz', SUPER)
    with db._get_cursor() as cur:
        anna = queries.load_access_context(cur, ANNA)
        head = queries.load_access_context(cur, HEAD)
        ex_head = queries.load_access_context(cur, EX_HEAD)
        nobody = queries.load_access_context(cur, 999)
    assert anna == {'user_id': ANNA, 'name': 'Анна Верификатор', 'role': 'operator', 'status': None,
                    'is_head': False, 'granted': True, 'email': 'anna@syntony.kz', 'link_state': 'unknown'}
    assert access.can_request_window(anna) and access.requires_qr(anna)
    assert head['is_head'] and not head['granted'] and not access.requires_qr(head)
    # Глава закрытого отдела — рядовой: QR у него спрашивают.
    assert not ex_head['is_head'] and access.requires_qr(ex_head)
    assert nobody is None


def test_list_is_alphabetical_with_who_granted_and_fired_stay_visible(db):
    with db._get_cursor() as cur:
        for user_id, email in ((SV, 'sv@icore.kz'), (ANNA, 'anna@icore.kz'), (FIRED, 'gleb@icore.kz')):
            assert queries.grant(cur, user_id, email, SUPER)
    with db._get_cursor() as cur:
        items = queries.list_access(cur)
        one = queries.get_item(cur, ANNA)
        missing = queries.get_item(cur, BORIS)
    assert [item['id'] for item in items] == [ANNA, SV, FIRED]
    assert [item['working'] for item in items] == [True, True, False]
    assert one['name'] == 'Анна Верификатор' and one['department'] == 'Отдел продаж'
    assert one['granted_by'] == 'Супер Админов' and one['link_state'] == 'unknown'
    assert one['granted_at'] and 'T' in one['granted_at'] and one['link_checked_at'] is None
    assert missing is None


def test_adding_twice_keeps_the_first_row(db):
    with db._get_cursor() as cur:
        assert queries.grant(cur, ANNA, 'first@icore.kz', SUPER) is True
        assert queries.grant(cur, ANNA, 'second@icore.kz', SV) is False
    with db._get_cursor() as cur:
        assert queries.get_item(cur, ANNA)['email'] == 'first@icore.kz'


def test_search_finds_working_people_outside_the_list_by_name_or_login(db):
    with db._get_cursor() as cur:
        queries.grant(cur, SV, 'sv@icore.kz', SUPER)
    with db._get_cursor() as cur:
        everyone = queries.search_people(cur, '')
        by_name = queries.search_people(cur, 'верификатор')
        by_login = queries.search_people(cur, 'ANNA')
        literal_percent = queries.search_people(cur, '100%')
        only_percent = queries.search_people(cur, '%')
        limited = queries.search_people(cur, '', limit=2)
    ids = [person['id'] for person in everyone]
    assert SV not in ids and FIRED not in ids
    assert ids == [ANNA, BORIS, EX_HEAD, HEAD, SUPER]          # по алфавиту
    assert [person['id'] for person in by_name] == [ANNA]
    assert [person['id'] for person in by_login] == [ANNA]
    assert [person['id'] for person in literal_percent] == [BORIS]
    assert [person['id'] for person in only_percent] == [BORIS]   # «%» — знак, а не «любой»
    assert len(limited) == 2
    card = {person['id']: person['card_email'] for person in everyone}
    assert card[ANNA] == 'anna@icore.kz' and card[BORIS] == '' and card[HEAD] == ''


def test_person_tells_working_and_card_email(db):
    with db._get_cursor() as cur:
        assert queries.person(cur, ANNA) == {'id': ANNA, 'name': 'Анна Верификатор', 'working': True,
                                             'card_email': 'anna@icore.kz'}
        assert queries.person(cur, FIRED)['working'] is False
        assert queries.person(cur, 999) is None


def test_one_email_belongs_to_one_person(db):
    import psycopg2
    with db._get_cursor() as cur:
        queries.grant(cur, ANNA, 'shared@icore.kz', SUPER)
        queries.grant(cur, SV, '', SUPER)
        queries.grant(cur, BORIS, '', SUPER)      # без почты — сколько угодно
    with db._get_cursor() as cur:
        assert queries.email_owner(cur, 'shared@icore.kz', SV) == 'Анна Верификатор'
        assert queries.email_owner(cur, 'shared@icore.kz', ANNA) is None
        assert queries.email_owner(cur, '', SV) is None
        assert queries.email_owner(cur, 'free@icore.kz', SV) is None
    with pytest.raises(psycopg2.errors.UniqueViolation):
        with db._get_cursor() as cur:
            queries.set_email(cur, SV, 'shared@icore.kz')


def test_new_email_resets_the_link_and_same_email_changes_nothing(db):
    with db._get_cursor() as cur:
        queries.grant(cur, ANNA, 'old@icore.kz', SUPER)
        assert queries.set_link_state(cur, ANNA, rules.LINK_LINKED) is True
    with db._get_cursor() as cur:
        linked = queries.get_item(cur, ANNA)
        assert linked['link_state'] == 'linked' and linked['link_checked_at']
        assert queries.set_email(cur, ANNA, 'old@icore.kz') is False
        assert queries.get_item(cur, ANNA)['link_state'] == 'linked'
        assert queries.set_email(cur, ANNA, 'new@icore.kz') is True
        changed = queries.get_item(cur, ANNA)
        assert (changed['email'], changed['link_state'], changed['link_checked_at']) == ('new@icore.kz', 'unknown', None)
        assert queries.set_email(cur, BORIS, 'x@icore.kz') is None
        assert queries.set_link_state(cur, BORIS, rules.LINK_LINKED) is False


def test_revoke_returns_the_email_and_log_keeps_names(db):
    with db._get_cursor() as cur:
        queries.grant(cur, ANNA, 'anna@icore.kz', SUPER)
        queries.log(cur, 'grant', user_id=ANNA, user_name='Анна Верификатор', email='anna@icore.kz',
                    actor_id=SUPER, actor_name='Супер Админов')
        assert queries.revoke(cur, ANNA) == 'anna@icore.kz'
        assert queries.revoke(cur, ANNA) is None
        queries.log(cur, 'revoke', user_id=ANNA, user_name='Анна Верификатор', email='anna@icore.kz',
                    actor_id=SUPER, actor_name='Супер Админов')
    with db._get_cursor() as cur:
        cur.execute("SELECT action, user_id, user_name, email, actor_id, actor_name, created_at IS NOT NULL "
                    "FROM syntony_window_access_log ORDER BY id")
        assert cur.fetchall() == [
            ('grant', ANNA, 'Анна Верификатор', 'anna@icore.kz', SUPER, 'Супер Админов', True),
            ('revoke', ANNA, 'Анна Верификатор', 'anna@icore.kz', SUPER, 'Супер Админов', True),
        ]
        assert queries.load_access_context(cur, ANNA)['granted'] is False


def test_deleting_the_account_takes_the_access_row_with_it(db):
    with db._get_cursor() as cur:
        queries.grant(cur, BORIS, 'boris@icore.kz', SUPER)
        cur.execute('DELETE FROM users WHERE id = %s', (BORIS,))
    with db._get_cursor() as cur:
        assert queries.list_access(cur) == []


class _Reply:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.content = b'{}'

    def json(self):
        return self._payload


def test_routes_over_real_sql_register_and_follow_the_link(db):
    from flask import Flask
    from syntony_window.routes import build_syntony_window_blueprint

    state = {'user': SUPER, 'qr': set(), 'calls': [],
             'reply': _Reply(409, {'error': {'code': 'operator_not_linked', 'message': 'Не связан'}})}

    def post(url, **kwargs):
        state['calls'].append(kwargs['json'])
        return state['reply']

    app = Flask(__name__)
    app.register_blueprint(build_syntony_window_blueprint(
        db=db,
        require_api_key=lambda handler: handler,
        build_cors_preflight_response=lambda: ('', 204),
        resolve_requester=lambda: (state['user'], None, None),
        sensitive_access_granted=lambda user_id: user_id in state['qr'],
        load_config=lambda: client.Config(url=client.DEFAULT_URL, key=KEY),
        request_window=lambda config, body: client.request_window(config, body, post=post),
    ))
    http = app.test_client()

    # Супер-админ добавляет верификатора: почта из карточки, запись у Syntony сразу.
    added = http.post('/api/syntony/access', json={'user_id': ANNA}).get_json()
    assert added['item']['email'] == 'anna@icore.kz' and added['item']['link_state'] == 'not_linked'
    assert state['calls'] == [{'scope': 'inbox', 'partnerUserId': str(ANNA), 'partnerUserName': 'Анна Верификатор',
                               'partnerUserEmail': 'anna@icore.kz', 'theme': 'light', 'locale': 'ru'}]

    # Чужая почта не проходит, своя — меняется и уходит в Syntony заново.
    http.post('/api/syntony/access', json={'user_id': SV})
    taken = http.patch('/api/syntony/access/%d' % SV, json={'email': 'ANNA@icore.kz'})
    assert (taken.status_code, taken.get_json()['code']) == (409, 'SYNTONY_EMAIL_TAKEN')
    http.patch('/api/syntony/access/%d' % ANNA, json={'email': 'anna@syntony.kz'})
    assert state['calls'][-1]['partnerUserEmail'] == 'anna@syntony.kz'

    # Оператор: без QR — отказ и ни одного запроса к Syntony; после QR — её ответ как есть.
    state['user'] = ANNA
    asked = len(state['calls'])
    locked = http.post('/api/syntony/window', json={'scope': 'inbox'})
    assert (locked.status_code, rules.error_code(locked.get_json())) == (403, rules.CODE_QR_REQUIRED)
    assert len(state['calls']) == asked
    state['qr'].add(ANNA)
    waiting = http.post('/api/syntony/window', json={'scope': 'inbox'})
    assert (waiting.status_code, rules.error_code(waiting.get_json())) == (409, 'operator_not_linked')

    # Syntony связала оператора: окно открывается, список это запоминает.
    state['reply'] = _Reply(200, {'url': WINDOW_URL, 'expiresAt': '2026-11-11T09:12:00Z'})
    opened = http.post('/api/syntony/window', json={'scope': 'leads', 'theme': 'dark'})
    assert opened.status_code == 200 and opened.get_json()['url'] == WINDOW_URL
    assert (state['calls'][-1]['scope'], state['calls'][-1]['theme']) == ('leads', 'dark')
    state['user'] = SUPER
    listed = {item['id']: item for item in http.get('/api/syntony/access').get_json()['items']}
    assert listed[ANNA]['link_state'] == 'linked' and listed[ANNA]['link_checked_at']
    assert listed[SV]['link_state'] == 'not_linked'

    # Сняли доступ — окна нет, в следе три выдачи почты и одно снятие.
    assert http.delete('/api/syntony/access/%d' % ANNA).status_code == 200
    state['user'] = ANNA
    closed = http.post('/api/syntony/window', json={'scope': 'inbox'})
    assert (closed.status_code, rules.error_code(closed.get_json())) == (403, rules.CODE_NO_ACCESS)
    with db._get_cursor() as cur:
        cur.execute('SELECT action FROM syntony_window_access_log ORDER BY id')
        assert [row[0] for row in cur.fetchall()] == ['grant', 'grant', 'email', 'revoke']
