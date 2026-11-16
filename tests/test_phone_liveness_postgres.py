# -*- coding: utf-8 -*-
"""Пульс телефона — настоящим SQL.

PHONE_LIVENESS_TEST_PORT (или LINE_WATCH_TEST_PORT) — порт локального Postgres: своя схема со
случайным именем, в конце удаляется; ни боевых учёток, ни импорта приложения. Запуск:

    PHONE_LIVENESS_TEST_PORT=55461 python -m pytest tests/test_phone_liveness_postgres.py

Что сторожится:
* строка оператора — его последнее событие ТЕЛЕФОНА (выгрузки не в счёт) и пульс сессий ТЕЛЕФОНА
  (вкладка сайта не в счёт); в ответе только открытые статусы и закрытые самим сервером;
* отделы — по коду, «*» — все;
* статус для возврата — тот, что стоял перед закрытием;
* полный круг на живых таблицах: телефон умер — статус закрыт, телефон вернулся — продолжен,
  повторные обходы ничего не добавляют.

Люди выдуманы.
"""
import contextlib
import os
import uuid
from datetime import datetime, timedelta

import pytest

from phone_liveness import rules, store
from phone_liveness.service import PhoneLivenessService

PORT = os.environ.get('PHONE_LIVENESS_TEST_PORT') or os.environ.get('LINE_WATCH_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (PHONE_LIVENESS_TEST_PORT)')

NOW = datetime(2026, 11, 16, 12, 0, 0)

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, department_id integer);
    CREATE TABLE operator_status_events (
        id serial PRIMARY KEY,
        operator_id integer NOT NULL,
        event_at timestamp NOT NULL,
        status_key text NOT NULL,
        state_note text,
        event_kind text,
        client_event_id text
    );
    CREATE UNIQUE INDEX uq_events_client ON operator_status_events (operator_id, client_event_id)
        WHERE client_event_id IS NOT NULL;
    CREATE TABLE user_sessions (
        session_id text PRIMARY KEY,
        user_id integer NOT NULL,
        user_agent text,
        last_seen_at timestamp,
        revoked_at timestamp
    );
    INSERT INTO departments (id, code) VALUES (1, 'op'), (2, 'TEZ'), (3, 'szov');
    INSERT INTO users (id, name, department_id) VALUES
        (11, 'Выдуманная Алия', 1), (12, 'Придуманный Ерлан', 1), (21, 'Сочинённая Дана', 2),
        (31, 'Небывалый Марат', 3), (41, 'Вымышленная Айгуль', NULL);
'''


class _Db:
    def __init__(self, schema):
        self.schema = schema

    def connect(self):
        import psycopg2
        # Десять секунд, а не три: под полным прогоном в четыре потока локальная база отвечает не сразу.
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                                connect_timeout=10)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.schema)
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

    def rows(self, sql, params=()):
        with self._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def run(self, sql, params=()):
        with self._get_cursor() as cur:
            cur.execute(sql, params)

    # Как db.append_operator_status_event в главном: одно событие на client_event_id.
    def append_operator_status_event(self, operator_id, event_at, status_key, state_note=None,
                                     event_kind=None, client_event_id=None):
        with self._get_cursor() as cur:
            cur.execute("""
                INSERT INTO operator_status_events (operator_id, event_at, status_key, state_note, client_event_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (operator_id, client_event_id) WHERE client_event_id IS NOT NULL DO NOTHING
                RETURNING id
            """, (operator_id, event_at, status_key, state_note, client_event_id))
            return {'duplicate': cur.fetchone() is None}

    def event(self, operator_id, minutes_ago, key, live=True, note=None):
        self.run("""
            INSERT INTO operator_status_events (operator_id, event_at, status_key, state_note, client_event_id)
            VALUES (%s, %s, %s, %s, %s)
        """, (operator_id, NOW - timedelta(minutes=minutes_ago), key, note,
              uuid.uuid4().hex if live else None))

    def session(self, user_id, minutes_ago, agent=rules.PHONE_USER_AGENT, revoked=False):
        self.run("""
            INSERT INTO user_sessions (session_id, user_id, user_agent, last_seen_at, revoked_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (uuid.uuid4().hex, user_id, agent, NOW - timedelta(minutes=minutes_ago),
              NOW if revoked else None))


@pytest.fixture
def db():
    schema = 't_phone_liveness_' + uuid.uuid4().hex
    base = _Db(schema)
    admin = base.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA IF EXISTS ' + schema + ' CASCADE')
        admin.close()


def load(db, codes=('op', 'tez'), now=NOW):
    with db._get_cursor() as cur:
        return {r['operator_id']: r for r in store.load_rows(cur, codes, now)}


def test_row_is_the_last_phone_event_and_the_phone_pulse(db):
    db.event(11, 300, 'перезвон')
    db.event(11, 120, 'Готов ')                      # ключ приходит как есть — сравниваем без регистра
    db.event(11, 10, 'Выключен', live=False)         # выгрузка кабинета — не событие телефона
    db.session(11, 200)
    db.session(11, 45)                               # второй телефон того же человека
    db.session(11, 2, agent='Mozilla/5.0')           # вкладка сайта пульсом телефона не считается
    db.session(11, 44, revoked=True)                 # отозванная сессия: когда была жива — тоже знак
    rows = load(db)
    assert set(rows) == {11}
    assert (rows[11]['key'], rows[11]['note']) == ('готов', None)
    assert rows[11]['event_at'] == NOW - timedelta(minutes=120)
    assert rows[11]['pulse'] == NOW - timedelta(minutes=44)
    # Вкладка сайта — признак жизни человека (seen), но не телефона (pulse).
    assert rows[11]['seen'] == NOW - timedelta(minutes=2)
    assert rules.phone_owned(rows[11])
    assert not rules.wants_close(rows[11], NOW)
    db.run("DELETE FROM user_sessions WHERE user_agent = 'Mozilla/5.0'")
    assert rules.wants_close(load(db)[11], NOW)


def test_browser_workplace_status_is_loaded_but_not_owned_by_a_phone(db):
    """Чат-менеджер: статус из браузера, сессии телефона нет — обход его не трогает."""
    db.event(12, 300, 'готов')
    db.session(12, 250, agent='Mozilla/5.0')
    row = load(db)[12]
    assert (row['pulse'], row['seen']) == (None, NOW - timedelta(minutes=250))
    assert not rules.wants_close(row, NOW)
    service = PhoneLivenessService(db, ['op'], now=lambda: NOW, monotonic=lambda: 3600.0)
    service._started = 0.0
    db.session(11, 1)                                # чей-то живой телефон — учёт активности работает
    assert service.sweep() == {'closed': 0, 'restored': 0, 'held': None}
    assert db.rows("SELECT COUNT(*) FROM operator_status_events WHERE operator_id = 12") == [(1,)]


def test_only_open_or_server_closed_statuses_are_loaded(db):
    db.event(11, 90, 'готов')
    db.event(11, 60, 'выключен')                                  # сам вышел
    db.event(12, 90, 'перерыв')
    db.event(12, 60, 'выключен', note=rules.CLOSE_NOTE)           # закрыл сервер
    db.event(21, 500, 'исход')                                    # сессий нет вовсе
    rows = load(db)
    assert set(rows) == {12, 21}
    assert rules.closed_by_server(rows[12])
    assert (rows[21]['pulse'], rows[21]['seen']) == (None, None)


def test_departments_by_code_and_star_for_everyone(db):
    for operator_id in (11, 21, 31, 41):
        db.event(operator_id, 50, 'готов')
    assert set(load(db)) == {11, 21}                 # код отдела сравнивается без регистра
    assert set(load(db, codes=('szov',))) == {31}
    assert set(load(db, codes=('*',))) == {11, 21, 31, 41}
    assert load(db, codes=()) == {}


def test_old_events_are_out_of_sight(db):
    db.event(11, (rules.LOOKBACK_DAYS + 1) * 24 * 60, 'готов')
    assert load(db) == {}


def test_freshest_pulse_is_about_phones_only(db):
    with db._get_cursor() as cur:
        assert store.freshest_pulse(cur, NOW) is None
    db.session(11, 7)
    db.session(12, 1, agent='Mozilla/5.0')
    db.session(21, 3 * 24 * 60)
    with db._get_cursor() as cur:
        assert store.freshest_pulse(cur, NOW) == NOW - timedelta(minutes=7)


def test_previous_status_is_what_stood_before_the_close(db):
    db.event(11, 200, 'готов')
    db.event(11, 150, 'перерыв')
    db.event(11, 100, 'выключен', note=rules.CLOSE_NOTE)
    db.event(11, 120, 'Готов', live=False)           # выгрузка между ними не в счёт
    row = load(db)[11]
    with db._get_cursor() as cur:
        assert store.previous_status(cur, row) == 'перерыв'
    # Перед закрытием стоял выход — продолжать нечего.
    db.event(12, 150, 'выключен')
    db.event(12, 100, 'выключен', note=rules.CLOSE_NOTE)
    with db._get_cursor() as cur:
        assert store.previous_status(cur, load(db)[12]) is None


def test_full_circle_dead_phone_closed_then_continued(db):
    clock = {'now': NOW, 'uptime': 3600.0}
    service = PhoneLivenessService(db, ['op', 'tez'], now=lambda: clock['now'], monotonic=lambda: clock['uptime'])
    # Сервис запомнил момент старта при создании — «сервер работает час».
    service._started = 0.0
    db.event(11, 180, 'готов')
    db.session(11, 45)                               # телефон умер 45 минут назад
    db.event(12, 200, 'исход')
    db.session(12, 2)                                # сосед работает
    assert service.sweep() == {'closed': 1, 'restored': 0, 'held': None}
    events = db.rows("SELECT status_key, state_note, event_at FROM operator_status_events "
                     "WHERE operator_id = 11 ORDER BY event_at, id")
    assert events[-1] == ('выключен', rules.CLOSE_NOTE, NOW - timedelta(minutes=35))
    # Повторный обход того же молчания ничего не добавляет.
    assert service.sweep() == {'closed': 0, 'restored': 0, 'held': None}

    # Через час телефон вернулся (сеть восстановилась) и вышел на связь.
    clock['now'] = NOW + timedelta(minutes=60)
    db.run("UPDATE user_sessions SET last_seen_at = %s WHERE user_id = 11", (NOW + timedelta(minutes=58),))
    db.run("UPDATE user_sessions SET last_seen_at = %s WHERE user_id = 12", (NOW + timedelta(minutes=59),))
    assert service.sweep() == {'closed': 0, 'restored': 1, 'held': None}
    events = db.rows("SELECT status_key, state_note, event_at FROM operator_status_events "
                     "WHERE operator_id = 11 ORDER BY event_at, id")
    # Телефон вернулся без нового события — значит, работал: статус продолжен с момента закрытия.
    assert events[-1] == ('готов', rules.RESTORE_NOTE, NOW - timedelta(minutes=35) + timedelta(seconds=1))
    assert service.sweep() == {'closed': 0, 'restored': 0, 'held': None}
    assert len(events) == 3
    # Соседа обходы не тронули.
    assert db.rows("SELECT COUNT(*) FROM operator_status_events WHERE operator_id = 12") == [(1,)]


def test_hang_window_is_the_gap_between_two_phone_events(db):
    db.event(11, 600, 'перезвон')
    db.event(11, 500, 'Готов')
    db.event(11, 300, 'Выключен', live=False)        # выгрузка внутри отрезка — не событие телефона
    db.event(11, 60, 'перезвон')
    with db._get_cursor() as cur:
        last, following = store.hang_window(cur, 11, NOW - timedelta(minutes=200))
        assert (last['key'], last['event_at']) == ('готов', NOW - timedelta(minutes=500))
        assert (following['key'], following['event_at']) == ('перезвон', NOW - timedelta(minutes=60))
        # После последнего события следующего нет; до первого — нет ничего.
        last, following = store.hang_window(cur, 11, NOW)
        assert (last['key'], following) == ('перезвон', None)
        assert store.hang_window(cur, 11, NOW - timedelta(minutes=700)) == (None, None)
        assert store.hang_window(cur, 12, NOW) == (None, None)


def test_past_hang_is_closed_by_hand_once(db):
    service = PhoneLivenessService(db, ['*'], now=lambda: NOW, monotonic=lambda: 0.0)
    db.event(11, 600, 'готов')
    db.event(11, 60, 'перезвон')                     # телефон запустили снова
    result = service.close_hang(11, NOW - timedelta(minutes=300))
    assert result['closed'] and result['hours_removed'] == 8.8
    events = db.rows("SELECT status_key, state_note, event_at FROM operator_status_events "
                     "WHERE operator_id = 11 ORDER BY event_at, id")
    assert events[1] == ('выключен', rules.CLOSE_NOTE, NOW - timedelta(minutes=590))
    assert len(events) == 3
    # Повтор находит между событиями уже закрытый отрезок — закрывать нечего.
    with pytest.raises(ValueError):
        service.close_hang(11, NOW - timedelta(minutes=300))
    assert db.rows("SELECT COUNT(*) FROM operator_status_events WHERE operator_id = 11") == [(3,)]


def test_phone_restart_needs_no_restore(db):
    """Телефон запустили заново — он сам прислал статус: серверу продолжать нечего."""
    service = PhoneLivenessService(db, ['op'], now=lambda: NOW, monotonic=lambda: 3600.0)
    service._started = 0.0
    db.event(11, 300, 'готов')
    db.event(11, 200, 'выключен', note=rules.CLOSE_NOTE)
    db.event(11, 5, 'перезвон')
    db.session(11, 4)
    assert service.sweep() == {'closed': 0, 'restored': 0, 'held': None}
