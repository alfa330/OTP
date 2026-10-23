# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: WATER_TEST_PORT — порт локального Postgres.

Доступ к выдаче воды по офисам (владелец 23.10.2026: «выбираем офис, далее из
фронт-офисников выбираем людей, которым открыт данный раздел, и выдаём доступ,
чтобы они могли выдавать воду только по доступным им офисам»).
tests/test_water_queries.py видит только, что запрос НАПИСАН. Что он ЗНАЧИТ —
кого переход «по их городу» пускает в какой офис, кто кандидат, как выдача и
снятие ложатся в таблицу и журнал — исполняется здесь, на настоящей схеме
раздела (water.schema) и маленьком наборе людей.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со
случайным именем, в конце она удаляется. Запуск на локальном кластере
(пароль, если он есть, — через PGPASSWORD):

    WATER_TEST_PORT=55433 python -m pytest tests/test_water_access_postgres.py

Люди выдуманы; id офисников — из списка water.access.ISSUER_USER_IDS, потому
что переход берёт именно его.
"""
import contextlib
import os
import uuid

import pytest

from water import access, queries, schema

PORT = os.environ.get('WATER_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (WATER_TEST_PORT)')

FRONT_OFFICE, SZOV = 909, 1
# Офисники из списка: Алматы, «алматы» с пробелами (как набрали в карточке),
# Астана, уволенный из Астаны, без города, из Шымкента (там офиса нет).
ALMATY_A, ALMATY_B, ASTANA_A, ASTANA_FIRED, NO_CITY, SHYMKENT = 419, 421, 425, 426, 509, 422
OUTSIDER = 10       # фронт-офис, но не из списка: раздел ему закрыт
HEAD = 532          # руководитель фронт-офисов: выдаёт везде, доступ ему не нужен
SUPER = 1
ACTOR = {'user_id': HEAD, 'name': 'Руководов Рустам'}

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL,
                        status text NOT NULL DEFAULT 'working', department_id integer, city text,
                        telegram_id bigint);
    CREATE TABLE wiki_offices (id integer PRIMARY KEY);
    INSERT INTO departments (id, code, name, head_user_id) VALUES
        (909, 'front_office', 'Фронт офисы', 532), (1, 'szov', 'СЗоВ', NULL);
    INSERT INTO users (id, name, role, status, department_id, city) VALUES
        (419, 'Алматов Арман', 'operator', 'working', 909, 'Алматы'),
        (421, 'Байзаков Бекзат', 'operator', 'working', 909, '  алматы '),
        (425, 'Астанов Асет', 'operator', 'working', 909, 'Астана'),
        (426, 'Бывшев Борис', 'operator', 'fired', 909, 'Астана'),
        (509, 'Безгородов Ерлан', 'operator', 'working', 909, NULL),
        (422, 'Шымкентов Шокан', 'operator', 'working', 909, 'Шымкент'),
        (10, 'Посторонов Павел', 'operator', 'working', 909, 'Алматы'),
        (532, 'Руководов Рустам', 'admin', 'working', 909, NULL),
        (1, 'Суперов Самат', 'super_admin', 'working', 1, NULL);
    INSERT INTO wiki_offices (id) VALUES (43), (47), (49);
'''


class _Db:
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
    def cursor(self):
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
    name = 't_water_access_' + uuid.uuid4().hex
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
    # Схема теста — не public: «таблицы доступа есть» отвечаем сами.
    monkeypatch.setattr(queries, 'access_ready', lambda cursor: True)
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + name + ' CASCADE')
        admin.commit()
        admin.close()


def rows(db, sql, params=None):
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def add_offices(db):
    """Офисы учёта: Алматы, Астана и выведенный из учёта алматинский."""
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO water_offices (office_id, city, name, address, stock, is_active) VALUES
                (43, 'Алматы', 'Офис «Бизнес»', 'Байзакова 78А', 50, TRUE),
                (49, 'Астана', 'Офис Астана', 'Сарыарка 31', 30, TRUE),
                (47, 'Алматы', 'Офис Алматы №1', 'Жамбыла 172В', 0, FALSE)
            RETURNING id""")
        return [row[0] for row in cur.fetchall()]


def init(db):
    """Повторный разворот схемы — как при каждом старте сервера."""
    with db.cursor() as cur:
        schema.init_water_schema(cur)


def grants(db):
    return sorted(rows(db, 'SELECT user_id, water_office_id FROM water_issuer_offices'))


def test_transition_gives_own_city_offices_once(db):
    """«По их городу»: алматинцы — офис Алматы, астанинцы — офис Астаны. Без
    города, из города без офиса, уволенный и не из списка — ничего; выведенный
    из учёта офис — никому."""
    # Первый разворот был на пустом учёте: переходить было не к чему.
    assert grants(db) == []
    almaty, astana, closed = add_offices(db)
    init(db)  # выкладка: офисы уже в учёте, журнал доступа пуст
    assert grants(db) == sorted([(ALMATY_A, almaty), (ALMATY_B, almaty), (ASTANA_A, astana)])
    log = rows(db, 'SELECT user_id, water_office_id, user_name, granted, actor_user_id, actor_name '
                   'FROM water_access_log ORDER BY user_id')
    assert log == [(ALMATY_A, almaty, 'Алматов Арман', True, None, None),
                   (ALMATY_B, almaty, 'Байзаков Бекзат', True, None, None),
                   (ASTANA_A, astana, 'Астанов Асет', True, None, None)]
    assert closed not in {office for _user, office in grants(db)}

    # Следующий разворот перехода не повторяет — ни сразу, ни после того, как
    # руководитель снял всё до последнего.
    init(db)
    assert len(rows(db, 'SELECT 1 FROM water_access_log')) == 3
    with db.cursor() as cur:
        queries.apply_access_changes(cur, grants=[], revokes=grants(db), actor=ACTOR)
    assert grants(db) == []
    init(db)
    assert grants(db) == []


def test_transition_waits_for_an_empty_log_only(db):
    """Руководитель успел что-то выдать раньше перехода — переход не нужен."""
    almaty, _astana, _closed = add_offices(db)
    with db.cursor() as cur:
        queries.apply_access_changes(cur, grants=[(NO_CITY, almaty)], revokes=[], actor=ACTOR)
    init(db)
    assert grants(db) == [(NO_CITY, almaty)]


def test_transition_skips_listed_ids_moved_out_of_front_office(db):
    """Офисник из списка, переведённый в СЗоВ, воду не выдаёт — и переход ему
    офис не открывает (разбор 23.10.2026: отбор по отделу тестом не держался)."""
    with db.cursor() as cur:
        cur.execute('UPDATE users SET department_id = %s WHERE id = %s', (SZOV, ALMATY_B))
    almaty, astana, _closed = add_offices(db)
    init(db)
    assert grants(db) == sorted([(ALMATY_A, almaty), (ASTANA_A, astana)])


def test_change_by_a_deleted_manager_is_not_shown_as_the_transition(db):
    """Учётку руководителя удалили — его id в журнале обнулился, а имя
    осталось: подпись окна — «Изменено · он», а не «Выдано по городу»."""
    almaty, _astana, _closed = add_offices(db)
    with db.cursor() as cur:
        queries.apply_access_changes(cur, grants=[(ALMATY_A, almaty)], revokes=[], actor=ACTOR)
        cur.execute('UPDATE departments SET head_user_id = NULL WHERE head_user_id = %s', (HEAD,))
        cur.execute('DELETE FROM users WHERE id = %s', (HEAD,))
    with db.cursor() as cur:
        state = queries.access_state(cur)
    changed = {office['id']: office['changed'] for office in state['offices']}[almaty]
    assert changed['by_name'] == 'Руководов Рустам'
    assert changed['by_seed'] is False


def test_candidates_are_listed_issuers_with_the_section_open(db):
    with db.cursor() as cur:
        people = queries.issuer_candidates(cur)
    # По имени, как в базе: «Алматов» < «Астанов» < «Байзаков»…
    assert [person['id'] for person in people] == [ALMATY_A, ASTANA_A, ALMATY_B, NO_CITY, SHYMKENT]
    assert {person['id']: person['city'] for person in people}[ALMATY_B] == 'алматы'
    assert {person['id']: person['city'] for person in people}[NO_CITY] is None


def test_state_and_changes(db):
    almaty, astana, closed = add_offices(db)
    init(db)
    with db.cursor() as cur:
        state = queries.access_state(cur)
    assert [(office['id'], office['user_ids']) for office in state['offices']] == [
        (almaty, [ALMATY_A, ALMATY_B]), (astana, [ASTANA_A])]
    assert all(office['changed']['by_seed'] for office in state['offices'])

    with db.cursor() as cur:
        changed = queries.apply_access_changes(
            cur, grants=[(ASTANA_A, almaty), (ALMATY_B, almaty), (NO_CITY, astana)],
            revokes=[(ALMATY_A, almaty), (SHYMKENT, astana)], actor=ACTOR)
    # Уже выданное и так не выданное ничего не пишут.
    assert sorted(changed) == sorted([(ASTANA_A, almaty, True), (NO_CITY, astana, True),
                                      (ALMATY_A, almaty, False)])
    with db.cursor() as cur:
        state = queries.access_state(cur)
        office_ids = queries.issue_office_ids(cur, ASTANA_A)
    assert [(office['id'], office['user_ids']) for office in state['offices']] == [
        (almaty, [ALMATY_B, ASTANA_A]), (astana, [ASTANA_A, NO_CITY])]
    assert office_ids == [almaty, astana]
    changed_by = {office['id']: office['changed'] for office in state['offices']}
    assert changed_by[almaty]['by_name'] == 'Руководов Рустам'
    assert changed_by[almaty]['by_seed'] is False
    log = rows(db, 'SELECT user_id, water_office_id, granted, actor_user_id FROM water_access_log '
                   'WHERE actor_user_id IS NOT NULL ORDER BY id')
    assert sorted(log) == sorted([(ASTANA_A, almaty, True, HEAD), (NO_CITY, astana, True, HEAD),
                                  (ALMATY_A, almaty, False, HEAD)])


def test_people_who_left_stay_in_the_table_but_not_in_the_window(db):
    almaty, _astana, _closed = add_offices(db)
    with db.cursor() as cur:
        queries.apply_access_changes(cur, grants=[(ALMATY_A, almaty), (OUTSIDER, almaty)],
                                     revokes=[], actor=ACTOR)
        cur.execute("UPDATE users SET status = 'fired' WHERE id = %s", (ALMATY_A,))
    with db.cursor() as cur:
        state = queries.access_state(cur)
    assert state['offices'][0]['user_ids'] == []
    assert grants(db) == sorted([(ALMATY_A, almaty), (OUTSIDER, almaty)])


def test_rows_leave_with_the_person(db):
    almaty, _astana, _closed = add_offices(db)
    with db.cursor() as cur:
        queries.apply_access_changes(cur, grants=[(SHYMKENT, almaty)], revokes=[], actor=ACTOR)
        cur.execute('DELETE FROM users WHERE id = %s', (SHYMKENT,))
    assert grants(db) == []
    # След остаётся: кому открывали — снимком имени.
    assert rows(db, 'SELECT user_id, user_name FROM water_access_log') == [(None, 'Шымкентов Шокан')]


def test_purchase_recipients_still_follow_the_section_rules(db):
    """notify_candidates делит с доступом сборку контекста — отбор прежний:
    кто выдаёт воду или ведёт учёт, без уволенных и посторонних."""
    with db.cursor() as cur:
        people = queries.notify_candidates(cur)
    ids = [person['id'] for person in people]
    assert set(ids) == {SUPER, HEAD, ALMATY_A, ALMATY_B, ASTANA_A, NO_CITY, SHYMKENT}
    assert all(access.ISSUE_DEPARTMENT_CODE == 'front_office' for _ in ids)
