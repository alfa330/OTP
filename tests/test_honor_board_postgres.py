# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: HONOR_TEST_PORT — порт локального Postgres.

«Доска почёта» (#318). tests/test_honor_board.py видит ручки на подменённом
слое запросов; что запросы ЗНАЧАТ — черновик и снимок в JSONB, блокировка
строки при сохранении, «первое число месяца», кого окно предлагает в
победители — исполняется здесь, на настоящей схеме (honor_board.schema).

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со
случайным именем, в конце она удаляется. Запуск на локальном кластере:

    HONOR_TEST_PORT=55491 python -m pytest tests/test_honor_board_postgres.py

Люди выдуманы.
"""
import contextlib
import os
import uuid
from datetime import date

import pytest

from honor_board import catalog, queries, schema

PORT = os.environ.get('HONOR_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (HONOR_TEST_PORT)')

OCTOBER, SEPTEMBER = date(2026, 10, 1), date(2026, 9, 1)

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text, name text NOT NULL,
                              is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE directions (id integer PRIMARY KEY, name text NOT NULL);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL,
                        status text NOT NULL DEFAULT 'working', department_id integer,
                        direction_id integer, avatar_bucket text, avatar_blob_path text);
    INSERT INTO departments (id, code, name, is_active) VALUES
        (20, 'op', 'Отдел продаж', TRUE), (21, 'OLD_OP', 'Старый ОП', FALSE), (1, 'szov', 'СЗоВ', TRUE);
    INSERT INTO directions (id, name) VALUES (5, 'Основа'), (6, 'Яндекс Регистрация');
    INSERT INTO users (id, name, role, status, department_id, direction_id, avatar_bucket, avatar_blob_path) VALUES
        (1, 'Тренерова Алия', 'trainer', 'working', 1, NULL, NULL, NULL),
        (4, 'Абенова Дана Маратовна', 'operator', 'working', 20, 5, 'b', 'avatars/4.webp'),
        (5, 'Жумабеков Ерлан', 'operator', 'annual_leave', 20, 6, 'b', ''),
        (6, 'ёлкина Вера', 'sv', 'working', 20, NULL, NULL, NULL),
        (7, 'Уволенов Борис', 'operator', 'fired', 20, 5, 'b', 'avatars/7.webp'),
        (8, 'Чужой Отдел', 'operator', 'working', 1, NULL, NULL, NULL);
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
def db():
    name = 't_honor_board_' + uuid.uuid4().hex
    base = _Db(name)
    admin = base.connect()
    try:
        with admin.cursor() as cur:
            cur.execute('CREATE SCHEMA ' + name)
            cur.execute('SET search_path TO ' + name)
            cur.execute(BASE_DDL)
            schema.init_honor_board_schema(cur)
            # Разворот идемпотентен: сервер повторяет его на каждом старте.
            schema.init_honor_board_schema(cur)
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


ENTRIES = catalog.normalize_entries('op', [
    {'nomination': 'best_employee', 'user_id': 4, 'name': 'Дана Абенова', 'description': 'Строка 1\nСтрока 2'},
])


def test_draft_publish_roundtrip(db):
    with db.cursor() as cur:
        board_id = queries.insert_board(cur, OCTOBER, 'op', ENTRIES, 1)
        assert board_id
        # Второй «первый» черновик той же доски — не новая строка, а отказ.
        assert queries.insert_board(cur, OCTOBER, 'op', ENTRIES, 1) is None
    with db.cursor() as cur:
        row = queries.board(cur, OCTOBER, 'op', lock=True)
        assert row['revision'] == 1
        assert row['draft_entries'] == ENTRIES
        assert row['updated_by_name'] == 'Тренерова Алия'
        assert row['published_at'] is None
        queries.update_draft(cur, row['id'], ENTRIES + [], 1)
        queries.publish(cur, row['id'], catalog.published_snapshot(ENTRIES), 1)
    with db.cursor() as cur:
        row = queries.board(cur, OCTOBER, 'op')
        assert row['revision'] == 2
        assert row['published_entries'] == catalog.published_snapshot(ENTRIES)
        assert row['published_by_name'] == 'Тренерова Алия'
        assert queries.published_periods(cur) == [OCTOBER]
        boards = queries.published_boards(cur, OCTOBER)
        assert [b['department_key'] for b in boards] == ['op']
        assert boards[0]['published_entries'][0]['description'] == 'Строка 1\nСтрока 2'
        assert len(queries.boards_between(cur, SEPTEMBER, OCTOBER)) == 1
        queries.unpublish(cur, row['id'])
    with db.cursor() as cur:
        row = queries.board(cur, OCTOBER, 'op')
        assert row['published_entries'] is None and row['published_at'] is None
        assert row['draft_entries'] == ENTRIES   # черновик остался
        assert queries.published_periods(cur) == []


def test_period_must_be_first_day(db):
    import psycopg2
    with pytest.raises(psycopg2.errors.CheckViolation):
        with db.cursor() as cur:
            queries.insert_board(cur, date(2026, 10, 2), 'op', ENTRIES, 1)


def test_previous_custom_titles(db):
    with db.cursor() as cur:
        draft = catalog.normalize_entries('front_office', [
            {'nomination': 'custom', 'title': 'Лучший кассир'}, {'nomination': 'custom', 'title': 'Душа офиса'}])
        first = queries.insert_board(cur, date(2026, 8, 1), 'front_office', draft, 1)
        september = catalog.normalize_entries('front_office', [
            {'nomination': 'best_employee', 'user_id': 4, 'name': 'Дана', 'description': 'Лучшая'},
            {'nomination': 'custom', 'title': 'Лучший администратор', 'user_id': 5, 'name': 'Ерлан',
             'description': 'Быстрый'},
            {'nomination': 'custom', 'title': 'Скорость ответа'}])
        second = queries.insert_board(cur, SEPTEMBER, 'front_office', september, 1)
        queries.publish(cur, second, catalog.published_snapshot(september), 1)
        assert first and second
    with db.cursor() as cur:
        # Последняя доска ДО месяца и её черновик: в снимке нет номинации без
        # победителя, а название её нужно и в следующем месяце.
        assert queries.previous_custom_titles(cur, OCTOBER, 'front_office') == [
            'Лучший администратор', 'Скорость ответа']
        assert queries.previous_custom_titles(cur, SEPTEMBER, 'front_office') == ['Лучший кассир', 'Душа офиса']
        assert queries.previous_custom_titles(cur, date(2026, 8, 1), 'front_office') == []


def test_people_and_candidates(db):
    with db.cursor() as cur:
        department = queries.department_by_code(cur, 'OP ')
        assert department == {'id': 20, 'name': 'Отдел продаж'}
        assert queries.department_by_code(cur, 'old_op') is None   # выключенный отдел не предлагается
        people = queries.candidates(cur, 20)
        # Уволенный — мимо; отпуск — не повод. Порядок здесь не сверяем: он от
        # правил сравнения кластера (кириллица под macOS libc идёт вразнобой),
        # окно упорядочивает список само (honorBoardMeta.js: sortPeople).
        assert {person['id'] for person in people} == {4, 5, 6}
        assert {person['id']: person['direction_name'] for person in people}[5] == 'Яндекс Регистрация'
        assert queries.existing_user_ids(cur, [4, 999, None]) == {4}
        assert queries.user_avatars(cur, [4, 5, 6]) == [(4, 'b', 'avatars/4.webp')]
        cards = queries.people_cards(cur, [7, 4])
        assert [(card['id'], card['direction_name']) for card in cards] == [(4, 'Основа'), (7, 'Основа')]
