# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: HONOR_TEST_PORT — порт локального Postgres.

«Доска почёта» (#318). tests/test_honor_board.py видит ручки на подменённом
слое запросов; что запросы ЗНАЧАТ — черновик и снимок в JSONB, блокировка
строки при сохранении, «первое число месяца», кого окно предлагает в
победители, последнее фото человека, вышедшие доски для колокола и перенос
старой таблицы (без first_published_at) — исполняется здесь, на настоящей
схеме (honor_board.schema).

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со
случайным именем, в конце она удаляется. Запуск на локальном кластере:

    HONOR_TEST_PORT=55491 python -m pytest tests/test_honor_board_postgres.py

Люди выдуманы.
"""
import contextlib
import json
import os
import select
import uuid
from datetime import date, datetime, timedelta

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


def test_previous_custom_nominations(db):
    with db.cursor() as cur:
        draft = catalog.normalize_entries('front_office', [
            {'nomination': 'custom', 'title': 'Лучший кассир', 'icon': 'Gem'},
            {'nomination': 'custom', 'title': 'Душа офиса'}])
        first = queries.insert_board(cur, date(2026, 8, 1), 'front_office', draft, 1)
        september = catalog.normalize_entries('front_office', [
            {'nomination': 'best_employee', 'user_id': 4, 'name': 'Дана', 'description': 'Лучшая'},
            {'nomination': 'custom', 'title': 'Лучший администратор', 'icon': 'Star', 'user_id': 5, 'name': 'Ерлан',
             'description': 'Быстрый'},
            {'nomination': 'custom', 'title': 'Скорость ответа', 'icon': 'Zap'}])
        second = queries.insert_board(cur, SEPTEMBER, 'front_office', september, 1)
        queries.publish(cur, second, catalog.published_snapshot(september), 1)
        # Доска ОП до 06.11.2026 — коды ТЗ без значков.
        legacy = [{'nomination': 'best_employee', 'title': 'Лучший сотрудник отдела', 'user_id': None, 'name': '',
                   'description': ''},
                  {'nomination': 'guru_sales', 'title': 'Гуру продаж', 'user_id': None, 'name': '', 'description': ''},
                  {'nomination': 'chat_star', 'title': 'Звезда чата', 'user_id': None, 'name': '', 'description': ''}]
        cur.execute("INSERT INTO honor_boards (period_month, department_key, draft_entries) VALUES (%s, 'op', %s::jsonb)",
                    (SEPTEMBER, json.dumps(legacy, ensure_ascii=False)))
        assert first and second
    with db.cursor() as cur:
        # Последняя доска ДО месяца и её черновик: в снимке нет номинации без
        # победителя, а название её нужно и в следующем месяце.
        assert queries.previous_custom_nominations(cur, OCTOBER, 'front_office') == [
            ('Лучший администратор', 'Star'), ('Скорость ответа', 'Zap')]
        assert queries.previous_custom_nominations(cur, SEPTEMBER, 'front_office') == [
            ('Лучший кассир', 'Gem'), ('Душа офиса', 'Award')]
        assert queries.previous_custom_nominations(cur, date(2026, 8, 1), 'front_office') == []
        assert queries.previous_custom_nominations(cur, OCTOBER, 'op') == [
            ('Гуру продаж', 'TrendingUp'), ('Звезда чата', 'MessageCircleHeart')]


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


def _almaty_now(cur):
    cur.execute("SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')")
    return cur.fetchone()[0]


def test_first_publication_and_unseen_boards(db):
    entries = catalog.normalize_entries('op', [
        {'nomination': 'best_employee', 'user_id': 4, 'name': 'Дана', 'description': 'Лучшая'}])
    with db.cursor() as cur:
        october = queries.insert_board(cur, OCTOBER, 'op', entries, 1)
        szov = queries.insert_board(cur, OCTOBER, 'szov', entries, 1)
        september = queries.insert_board(cur, SEPTEMBER, 'op', entries, 1)
        # Сентябрь вышел давно — за пределами памяти колокола.
        queries.publish(cur, september, catalog.published_snapshot(entries), 1)
        cur.execute("UPDATE honor_boards SET first_published_at = first_published_at - INTERVAL '%s days' WHERE id = %%s"
                    % (queries.NOTIFY_DAYS + 1), (september,))
        assert queries.unseen_periods(cur, 4, 5) == (0, [])
        assert queries.has_unseen(cur, 4) is False
        queries.publish(cur, october, catalog.published_snapshot(entries), 1)
        queries.publish(cur, szov, catalog.published_snapshot(entries), 1)
        cur.execute("SELECT first_published_at, published_at FROM honor_boards WHERE id = %s", (october,))
        first, published = cur.fetchone()
        assert first == published
    with db.cursor() as cur:
        total, rows = queries.unseen_periods(cur, 4, 5)
        assert total == 1 and rows[0][0] == OCTOBER and rows[0][1] == ['op', 'szov']
        assert queries.has_unseen(cur, 4) is True
        queries.mark_seen(cur, 4)
        queries.mark_seen(cur, 4)   # повтор — та же строка, а не вторая
        cur.execute("SELECT COUNT(*) FROM honor_board_reads WHERE user_id = 4")
        assert cur.fetchone()[0] == 1
        assert queries.unseen_periods(cur, 4, 5) == (0, [])
        # Правки и повторная публикация не сдвигают первую публикацию — второй раз не звоним.
        queries.publish(cur, october, catalog.published_snapshot(entries), 1)
        cur.execute("SELECT first_published_at FROM honor_boards WHERE id = %s", (october,))
        assert cur.fetchone()[0] == first
        assert queries.has_unseen(cur, 4) is False
        assert queries.has_unseen(cur, 6) is True   # другой человек ещё не видел
        # Снятая с публикации доска из колокола уходит.
        queries.unpublish(cur, october)
        queries.unpublish(cur, szov)
        assert queries.has_unseen(cur, 6) is False


def test_publisher_is_silenced_in_the_same_transaction(db):
    """Ручка /publish ставит first_published_at и водяной знак публикующего одной
    транзакцией: время одно и то же, и строгое «>» не будит самого тренера."""
    with db.cursor() as cur:
        board = queries.insert_board(cur, OCTOBER, 'op', ENTRIES, 1)
    with db.cursor() as cur:
        queries.publish(cur, board, catalog.published_snapshot(ENTRIES), 1)
        queries.mark_seen(cur, 1)
    with db.cursor() as cur:
        assert queries.has_unseen(cur, 1) is False
        assert queries.unseen_periods(cur, 1, 5) == (0, [])
        assert queries.has_unseen(cur, 4) is True


def test_republish_after_unpublish_keeps_the_first_publication(db):
    with db.cursor() as cur:
        board = queries.insert_board(cur, OCTOBER, 'op', ENTRIES, 1)
        queries.publish(cur, board, catalog.published_snapshot(ENTRIES), 1)
    with db.cursor() as cur:
        queries.mark_seen(cur, 4)
    with db.cursor() as cur:
        queries.unpublish(cur, board)
        cur.execute("SELECT first_published_at IS NOT NULL FROM honor_boards WHERE id = %s", (board,))
        assert cur.fetchone()[0] is True
    with db.cursor() as cur:
        queries.publish(cur, board, catalog.published_snapshot(ENTRIES), 1)
    with db.cursor() as cur:
        assert queries.has_unseen(cur, 4) is False


class _PublicAsSchema:
    """Установщик триггеров колокола ищет таблицы как public.<имя>; в тесте они в
    своей схеме — подменяем имя в параметре to_regclass."""

    def __init__(self, cursor, name):
        self.cursor, self.name = cursor, name

    def execute(self, sql, params=None):
        if params and isinstance(params, tuple) and isinstance(params[0], str) and params[0].startswith('public.'):
            params = (self.name + '.' + params[0][len('public.'):],) + params[1:]
        return self.cursor.execute(sql, params)

    def fetchone(self):
        return self.cursor.fetchone()


def test_bell_trigger_fires_only_on_publication_changes(db):
    """Окно тренера сохраняет черновик каждые 0,8 с: тычок колокола — только на
    первую публикацию, снятие и возврат на доску, а водяной знак — самому человеку."""
    from db.schema import SchemaMixin

    with db.cursor() as cur:
        SchemaMixin._init_bell_notify_schema_tx(None, _PublicAsSchema(cur, db.name))
        cur.execute("""SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                       WHERE n.nspname = %s AND NOT t.tgisinternal ORDER BY 2""", (db.name,))
        assert cur.fetchall() == [('honor_board_reads', 'trg_bell_honor_board_reads'),
                                  ('honor_boards', 'trg_bell_honor_boards')]
    listener = db.connect()
    listener.autocommit = True
    listener.cursor().execute('LISTEN bell_events')

    def heard():
        select.select([listener], [], [], 0.3)
        listener.poll()
        got = [note.payload for note in listener.notifies]
        listener.notifies.clear()
        return got

    try:
        with db.cursor() as cur:
            board = queries.insert_board(cur, OCTOBER, 'op', ENTRIES, 1)
        assert heard() == []
        with db.cursor() as cur:
            queries.update_draft(cur, board, ENTRIES, 1)
        assert heard() == []
        with db.cursor() as cur:
            queries.publish(cur, board, catalog.published_snapshot(ENTRIES), 1)
        assert heard() == ['{"b":1}']
        with db.cursor() as cur:
            queries.update_draft(cur, board, ENTRIES, 1)
            queries.publish(cur, board, catalog.published_snapshot(ENTRIES), 1)   # «Опубликовать правки»
        assert heard() == []
        with db.cursor() as cur:
            queries.unpublish(cur, board)
        assert heard() == ['{"b":1}']
        with db.cursor() as cur:
            queries.publish(cur, board, catalog.published_snapshot(ENTRIES), 1)
        assert heard() == ['{"b":1}']
        with db.cursor() as cur:
            queries.mark_seen(cur, 4)
        assert [json.loads(payload) for payload in heard()] == [{'u': [4]}]
    finally:
        listener.close()


def test_photos(db):
    with db.cursor() as cur:
        assert queries.user_department_code(cur, 4) == 'op'
        assert queries.user_department_code(cur, 5) == 'op'     # отпуск — не повод
        assert queries.user_department_code(cur, 7) is None     # уволенный
        assert queries.user_department_code(cur, 999) is None
        common = dict(bucket='b', content_type='image/webp', width=300, height=400, file_size=1000, actor_id=1)
        first = queries.insert_photo(cur, user_id=4, blob_path='HonorBoard/4/a.webp', cutout=True, **common)
        second = queries.insert_photo(cur, user_id=4, blob_path='HonorBoard/4/b.webp', cutout=False, **common)
        other = queries.insert_photo(cur, user_id=5, blob_path='HonorBoard/5/c.webp', cutout=True, **common)
    with db.cursor() as cur:
        # Загруженные, но не поставленные ни на одну доску фото не подставляются.
        assert queries.latest_photos(cur, [4, 5, 6]) == {}
        on_draft = catalog.normalize_entries('op', [
            {'nomination': 'best_employee', 'user_id': 4, 'name': 'Дана', 'photo_id': first},
            {'nomination': 'custom', 'title': 'Б', 'user_id': 5, 'name': 'Ерлан', 'photo_id': other}])
        board = queries.insert_board(cur, OCTOBER, 'op', on_draft, 1)
        queries.publish(cur, board, catalog.published_snapshot(on_draft), 1)
        later = catalog.normalize_entries('op', [
            {'nomination': 'best_employee', 'user_id': 4, 'name': 'Дана', 'photo_id': second},
            {'nomination': 'custom', 'title': 'Б', 'user_id': 5, 'name': 'Ерлан', 'photo_id': other}])
        queries.update_draft(cur, board, later, 1)
    with db.cursor() as cur:
        latest = queries.latest_photos(cur, [4, 5, 6])
        assert sorted(latest) == [4, 5]
        assert (latest[4]['id'], latest[4]['cutout'], latest[4]['blob_path']) == (second, False, 'HonorBoard/4/b.webp')
        assert latest[5]['id'] == other
        found = queries.photos_by_ids(cur, [first, other, 999])
        assert sorted(found) == sorted([first, other])
        assert found[first]['user_id'] == 4 and found[first]['cutout'] is True
        assert queries.photos_by_ids(cur, []) == {}
        assert queries.latest_photos(cur, []) == {}
        # Убрали фото из черновика — подставляется прежнее, которое стоит на
        # опубликованной доске.
        queries.update_draft(cur, board, on_draft[:1] + later[1:], 1)
        assert queries.latest_photos(cur, [4])[4]['id'] == first
        # Победитель на доске направления — фото можно и после ухода из отдела.
        assert queries.user_on_boards(cur, 7, ['op']) is False
        assert queries.user_on_boards(cur, 5, ['op']) is True
        assert queries.user_on_boards(cur, 5, ['szov', 'front_office']) is False
        assert queries.user_on_boards(cur, 5, []) is False


def test_old_table_is_migrated(db):
    """Таблица, развёрнутая коммитом 137a5fb0 (без first_published_at): разворот
    дописывает колонку и проставляет её выложенным доскам."""
    with db.cursor() as cur:
        cur.execute("ALTER TABLE honor_boards DROP COLUMN first_published_at")
        cur.execute("""INSERT INTO honor_boards (period_month, department_key, draft_entries, published_entries, published_at)
                       VALUES (%s, 'op', '[]'::jsonb, '[]'::jsonb, %s), (%s, 'szov', '[]'::jsonb, NULL, NULL)""",
                    (OCTOBER, datetime(2026, 11, 5, 11, 0), OCTOBER))
        schema.init_honor_board_schema(cur)
        cur.execute("SELECT department_key, first_published_at FROM honor_boards ORDER BY department_key")
        assert cur.fetchall() == [('op', datetime(2026, 11, 5, 11, 0)), ('szov', None)]
