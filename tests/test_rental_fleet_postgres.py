# -*- coding: utf-8 -*-
"""«Аренда авто» — наличие и направления в офис (#391) на настоящем Postgres: RENTAL_TEST_PORT.

Чистые правила (ключи моделей, телефон, поля направления, блоки условий) проверяет
tests/test_rental.py. Здесь — процесс на схеме раздела:

  * карточки и «Условия работы» заводятся из макета один раз;
  * свободные — только вручную и только аренда/руководители; снимок Yume их не трогает и даёт
    одни занятые; не указаны или 0 — направить нельзя (ТЗ 3.1);
  * направление проверяет поля, хранит снимок карточки, не меняет свободных (ТЗ 3.2);
  * реестр: оператору ОП — свои, аренде и руководителям — все; отборы;
  * статус и комментарий — аренда и руководители, каждое изменение в истории (ТЗ 4);
  * история и версии условий не правятся и не удаляются (ТЗ 10.3);
  * ручки: кому раздел закрыт, кому закрыто действие.

Только 127.0.0.1, своя схема со случайным именем, в конце она удаляется. Люди выдуманы.

    RENTAL_TEST_PORT=55432 python -m pytest tests/test_rental_fleet_postgres.py
"""

import contextlib
import os
import uuid
from datetime import datetime

import pytest

from rental import queries, schema
from rental.service import RentalService

PORT = os.environ.get('RENTAL_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (RENTAL_TEST_PORT)')

SUPER, SALES_HEAD, SALES_OP, SALES_OP2, RENTAL, RENTAL_HEAD, OOZ, STRANGER = 1, 10, 11, 12, 20, 21, 30, 40
NOW = datetime(2026, 10, 28, 12, 0)

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text UNIQUE, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL,
                        status text NOT NULL DEFAULT 'working', department_id integer);
    CREATE TABLE wiki_space_departments (space_id integer, department_id integer);
    CREATE TABLE wiki_taxi_parks (id serial PRIMARY KEY, name text NOT NULL, status text NOT NULL,
                                  position integer NOT NULL DEFAULT 0, space_id integer);
    INSERT INTO departments (id, code, name, head_user_id) VALUES
        (367, 'op', 'Отдел продаж', 10), (900, 'car_rental', 'Аренда авто', 21),
        (2008, 'request_processing_department', 'ООЗ', NULL), (1, 'szov', 'СЗоВ', NULL);
    INSERT INTO users (id, name, role, status, department_id) VALUES
        (1, 'Супер Админов', 'super_admin', 'working', 1),
        (10, 'Глава Продаж', 'admin', 'working', 367),
        (11, 'Оператор Продаж', 'operator', 'working', 367),
        (12, 'Второй Оператор', 'operator', 'working', 367),
        (20, 'Сотрудник Аренды', 'operator', 'working', 900),
        (21, 'Глава Аренды', 'operator', 'working', 900),
        (30, 'Ночной Оператор', 'operator', 'working', 2008),
        (40, 'Чужой Оператор', 'operator', 'working', 1);
    INSERT INTO wiki_space_departments VALUES (11, 367), (11, 1);
    INSERT INTO wiki_taxi_parks (name, status, position, space_id) VALUES
        ('iTaxi', 'active', 1, 11), ('Jana такси', 'active', 2, 11), ('Старый парк', 'archived', 3, 11),
        ('Чужое пространство', 'active', 4, 99);
'''


class _Db:
    def __init__(self, name):
        self.name = name

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user=os.environ.get('RENTAL_TEST_USER', 'postgres'),
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


@pytest.fixture
def db():
    name = 't_rentalfleet_' + uuid.uuid4().hex
    base = _Db(name)
    admin = base.connect()
    try:
        with admin.cursor() as cur:
            cur.execute('CREATE SCHEMA ' + name)
            cur.execute('SET search_path TO ' + name)
            cur.execute(BASE_DDL)
            schema.init_rental_schema(cur)
            schema.init_rental_schema(cur)   # рестарт прода: идемпотентно, без второго набора карточек
        admin.commit()
    except Exception:
        admin.rollback()
        admin.close()
        raise
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA %s CASCADE' % name)
        admin.commit()
        admin.close()


FLEET = [
    {'keys': ['hongqieqm5'], 'status': 2, 'label': 'Hongqi E-QM5'},
    {'keys': ['hongqieqm5'], 'status': 2, 'label': 'Hongqi E-QM5'},
    {'keys': ['hongqieqm5'], 'status': 1, 'label': 'Hongqi E-QM5'},
    {'keys': ['hongqieqm5'], 'status': 0, 'label': 'Hongqi E-QM5'},
    {'keys': ['geelyemgrand'], 'status': 2, 'deleted': True, 'label': 'Geely Emgrand'},
]


@pytest.fixture
def env(db):
    loads = []

    def loader():
        loads.append(1)
        return FLEET

    service = RentalService(cursor_factory=db._get_cursor, config_loader=lambda: {}, collector=None,
                            now=lambda: NOW, start_thread=lambda target: target(), fleet_loader=loader)
    return {'db': db, 'service': service, 'loads': loads}


def _ctx(env, user_id):
    return env['service'].context(user_id)


def _cards(db):
    with db._get_cursor() as cur:
        return queries.cards(cur)


def _eqm5(db):
    return [card for card in _cards(db) if card['title'] == 'HongQi E-QM5']


def test_seed_once_and_parks_from_sales_spaces(env):
    cards = _cards(env['db'])
    assert len(cards) == 15
    assert [c['insurance'] for c in _eqm5(env['db'])] == ['от водителя', 'Путевой лист']
    with env['db']._get_cursor() as cur:
        assert len(queries.conditions_versions(cur)) == 1
        assert queries.sales_parks(cur) == ['iTaxi', 'Jana такси']


def test_free_count_is_manual_and_only_for_rental(env):
    service, db = env['service'], env['db']
    card = _eqm5(db)[1]                                   # путевой лист — своё число свободных
    assert service.set_free_count(_ctx(env, SALES_OP), card['id'], 3)[0] == 'forbidden'
    assert service.set_free_count(_ctx(env, RENTAL), card['id'], -1)[0] == 'bad_input'
    outcome, updated = service.set_free_count(_ctx(env, RENTAL), card['id'], 2)
    assert outcome == 'ok' and (updated['free_count'], updated['free_by_name']) == (2, 'Сотрудник Аренды')
    assert _eqm5(db)[0]['free_at'] is None                # соседняя карточка модели не тронута
    # Снимок Yume обновляется, но ручное число не трогает: из Yume — только занятые.
    assert service.refresh_fleet(force=True) is True
    assert _eqm5(db)[1]['free_count'] == 2
    with db._get_cursor() as cur:
        log = queries.card_log(cur, card['id'])
    assert [(e['field'], e['old'], e['new']) for e in log] == [('free_count', None, '2')]


def test_direction_needs_free_cars_checks_fields_and_keeps_free_count(env):
    service, db = env['service'], env['db']
    card = _eqm5(db)[0]
    payload = {'card_id': card['id'], 'phone': '8 707 123 45 67', 'visit_date': '2026-10-29',
               'visit_time': '15:00', 'park': 'iTaxi', 'comment': 'будет после обеда'}
    assert service.create_direction(_ctx(env, SALES_OP), payload)[0] == 'no_free'      # не указано ни разу
    service.set_free_count(_ctx(env, RENTAL), card['id'], 0)
    assert service.create_direction(_ctx(env, SALES_OP), payload)[0] == 'no_free'      # «Нет в наличии»
    service.set_free_count(_ctx(env, RENTAL), card['id'], 3)
    assert service.create_direction(_ctx(env, SALES_OP), dict(payload, park='Старый парк'))[0] == 'bad_input'
    assert service.create_direction(_ctx(env, SALES_OP), dict(payload, visit_date='2026-10-27'))[0] == 'bad_input'
    assert service.create_direction(_ctx(env, OOZ), payload)[0] == 'forbidden'
    outcome, direction = service.create_direction(_ctx(env, SALES_OP), payload)
    assert outcome == 'ok'
    assert (direction['status'], direction['phone'], direction['card_terms'], direction['created_by_name']) == \
           ('waiting', '+77071234567', 'от водителя, от 7 и выше', 'Оператор Продаж')
    assert direction['visit_at'] == datetime(2026, 10, 29, 15, 0)
    assert _eqm5(db)[0]['free_count'] == 3                # направление машину не бронирует (ТЗ 3.2)


def test_registry_scope_filters_status_history_and_append_only(env):
    service, db = env['service'], env['db']
    card = _eqm5(db)[0]
    service.set_free_count(_ctx(env, RENTAL), card['id'], 5)
    base = {'card_id': card['id'], 'visit_date': '2026-10-29', 'visit_time': '10:00', 'park': 'iTaxi'}
    _, first = service.create_direction(_ctx(env, SALES_OP), dict(base, phone='87071110011'))
    service.create_direction(_ctx(env, SALES_OP2), dict(base, phone='87072220022', visit_date='2026-10-30'))
    with db._get_cursor() as cur:
        own, own_total = queries.directions(cur, own_by=SALES_OP)
        everything, total = queries.directions(cur)
        by_phone, _ = queries.directions(cur, phone_digits='2220022')
        by_day, _ = queries.directions(cur, date_from=datetime(2026, 10, 30).date(), date_to=datetime(2026, 10, 30).date())
    assert (own_total, total) == (1, 2)
    assert [d['phone'] for d in by_phone] == ['+77072220022'] and [d['phone'] for d in by_day] == ['+77072220022']

    assert service.update_direction(_ctx(env, SALES_OP), first['id'], {'status': 'came'})[0] == 'forbidden'
    assert service.update_direction(_ctx(env, RENTAL), first['id'], {'status': 'thinking'})[0] == 'bad_input'
    assert service.update_direction(_ctx(env, RENTAL), first['id'], {'status': 'came'})[0] == 'ok'
    outcome, updated = service.update_direction(_ctx(env, SALES_HEAD), first['id'],
                                                {'status': 'issued', 'comment': 'выдали BYD'})
    assert outcome == 'ok' and (updated['status'], updated['comment']) == ('issued', 'выдали BYD')
    with db._get_cursor() as cur:
        log = queries.direction_log(cur, first['id'])
    assert [(e['field'], e['old'], e['new'], e['by']) for e in log] == [
        ('status', 'waiting', 'came', 'Сотрудник Аренды'),
        ('status', 'came', 'issued', 'Глава Продаж'),
        ('comment', None, 'выдали BYD', 'Глава Продаж'),
    ]
    # История только пополняется (ТЗ 10.3).
    for statement in ('UPDATE rental_direction_log SET new_value = %s', 'DELETE FROM rental_conditions WHERE %s = %s'):
        with pytest.raises(Exception, match='только пополняется'):
            with db._get_cursor() as cur:
                cur.execute(statement, ('x',) if statement.startswith('UPDATE') else (1, 1))


def test_cards_edit_hide_and_conditions_versions(env):
    service, db = env['service'], env['db']
    card = _cards(db)[0]
    payload = dict(card, price='13 000', yume_keys='Toyota RAV4, RAV 4')
    assert service.save_card(_ctx(env, SALES_OP), payload, card['id'])[0] == 'forbidden'
    outcome, updated = service.save_card(_ctx(env, RENTAL_HEAD), payload, card['id'])
    assert outcome == 'ok' and (updated['price'], updated['yume_keys']) == (13000, ['toyotarav4', 'rav4'])
    assert service.hide_card(_ctx(env, RENTAL), card['id']) == 'ok'
    assert card['id'] not in [c['id'] for c in _cards(db)]
    assert service.create_direction(_ctx(env, SALES_OP), {'card_id': card['id']})[0] == 'no_card'

    blocks = [{'title': 'Условия аренды', 'kind': 'list', 'text': 'Первый день бесплатно'}]
    assert service.save_conditions(_ctx(env, SALES_OP), blocks)[0] == 'forbidden'
    assert service.save_conditions(_ctx(env, RENTAL), blocks)[0] == 'ok'
    assert service.save_conditions(_ctx(env, RENTAL), blocks)[0] == 'ok'     # то же — новой версии нет
    with db._get_cursor() as cur:
        versions = queries.conditions_versions(cur)
        first = queries.conditions_version(cur, versions[-1]['id'])
    assert [v['by'] for v in versions] == ['Сотрудник Аренды', 'Макет ТЗ #391']
    assert first['blocks'][0]['title'] == 'Условия аренды' and len(first['blocks']) == 5


# ─── Ручки ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(env, monkeypatch):
    from flask import Flask, jsonify, request

    from rental.routes import build_rental_blueprint

    monkeypatch.setattr(queries, 'now_almaty', lambda: NOW)
    app = Flask(__name__)
    app.register_blueprint(build_rental_blueprint(
        db=env['db'],
        require_api_key=lambda handler: handler,
        build_cors_preflight_response=lambda: jsonify({}),
        resolve_requester=lambda: (int(request.headers['X-User-Id']), {}, None),
        service=env['service'],
    ))
    return app.test_client()


def _as(user_id):
    return {'X-User-Id': str(user_id)}


def test_routes_by_role(client, env):
    assert client.get('/api/rental/fleet', headers=_as(STRANGER)).status_code == 403
    night_only = client.get('/api/rental/fleet', headers=_as(OOZ))
    assert night_only.status_code == 403 and night_only.get_json()['code'] == 'RENTAL_FORBIDDEN'
    screen = client.get('/api/rental', headers=_as(SALES_OP)).get_json()
    assert 'report' not in screen and screen['capabilities']['send'] is True

    fleet = client.get('/api/rental/fleet', headers=_as(SALES_OP)).get_json()
    assert env['loads'] == [1]                             # устаревший снимок обновился сам
    eqm5 = [c for c in fleet['cards'] if c['title'] == 'HongQi E-QM5']
    assert [c['busy'] for c in eqm5] == [{'in_rent': 2, 'booked': 1}] * 2
    assert all(c['free_count'] is None for c in fleet['cards'])
    assert 'yume_keys' not in eqm5[0] and fleet['parks'] == ['iTaxi', 'Jana такси']
    client.get('/api/rental/fleet', headers=_as(SALES_OP))
    assert env['loads'] == [1]                             # свежий снимок — Yume второй раз не спрашивают

    assert client.put('/api/rental/cards/%d/free' % eqm5[0]['id'], json={'free_count': 2},
                      headers=_as(SALES_OP)).status_code == 403
    assert client.put('/api/rental/cards/%d/free' % eqm5[0]['id'], json={'free_count': 2},
                      headers=_as(RENTAL)).get_json()['card']['free_count'] == 2
    created = client.post('/api/rental/directions', headers=_as(SALES_OP), json={
        'card_id': eqm5[0]['id'], 'phone': '+7 707 333 44 55', 'visit_date': '2026-10-28', 'visit_time': '18:00',
        'park': 'Jana такси'})
    assert created.status_code == 201 and created.get_json()['direction']['status_label'] == 'Ожидаем'
    direction_id = created.get_json()['direction']['id']

    other = client.get('/api/rental/directions', headers=_as(SALES_OP2)).get_json()
    assert other['total'] == 0 and other['all'] is False
    rental_view = client.get('/api/rental/directions?status=waiting&q=3334455', headers=_as(RENTAL)).get_json()
    assert rental_view['total'] == 1 and rental_view['operators'] == [{'id': SALES_OP, 'name': 'Оператор Продаж'}]
    assert client.patch('/api/rental/directions/%d' % direction_id, json={'status': 'no_show'},
                        headers=_as(SALES_OP)).status_code == 403
    assert client.patch('/api/rental/directions/%d' % direction_id, json={'status': 'no_show'},
                        headers=_as(RENTAL)).get_json()['direction']['status_label'] == 'Не пришёл в офис'
    log = client.get('/api/rental/directions/%d/log' % direction_id, headers=_as(SALES_OP)).get_json()['items']
    assert [(e['label'], e['old'], e['new']) for e in log] == [('Статус', 'Ожидаем', 'Не пришёл в офис')]
    assert client.get('/api/rental/directions/%d/log' % direction_id, headers=_as(SALES_OP2)).status_code == 404

    conditions = client.get('/api/rental/conditions', headers=_as(SALES_OP)).get_json()
    assert conditions['can_edit'] is False and len(conditions['current']['blocks']) == 5
    assert client.post('/api/rental/conditions', json={'blocks': []}, headers=_as(RENTAL)).status_code == 400
