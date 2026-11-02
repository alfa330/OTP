# -*- coding: utf-8 -*-
"""Заявки на отпуск и Б/С (#358) на настоящем Postgres: LEAVE_TEST_PORT — порт
локального кластера.

Чистые правила — tests/test_leave_requests_rules.py. Здесь — весь маршрут на
схеме пакета: подача, шаг СВ, предложение дат и ответ оператора, утверждение
главой с записью периода в график, пересечения с отпусками коллег, колокол и
Telegram. Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема
со случайным именем, в конце она удаляется. Люди выдуманы.

    LEAVE_TEST_PORT=55481 python -m pytest tests/test_leave_requests_postgres.py
"""
import ast
import contextlib
import os
import textwrap
import uuid
from datetime import date, datetime, timedelta

import pytest

from leave_requests import notify, queries, schema, service
from leave_requests.rules import LeaveError
from notifications import sources

PORT = os.environ.get('LEAVE_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (LEAVE_TEST_PORT)')

TODAY = date(2026, 11, 2)
ADMIN, HEAD, HEAD_OP, SV_A, SV_B, SV_OP = 1, 100, 200, 300, 301, 302
OP_A, OP_B, OP_C, OP_NOSV, OP_OP = 400, 401, 402, 403, 404

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, code text UNIQUE, name text NOT NULL,
                              head_user_id integer, is_active boolean NOT NULL DEFAULT TRUE);
    CREATE TABLE directions (id integer PRIMARY KEY, name text NOT NULL);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, role text NOT NULL,
                        status text NOT NULL DEFAULT 'working', department_id integer,
                        direction_id integer, telegram_id bigint);
    CREATE TABLE groups (id integer PRIMARY KEY, name text NOT NULL);
    CREATE TABLE group_operator_memberships (id serial PRIMARY KEY, group_id integer, operator_id integer,
                                             start_date date NOT NULL, end_date date);
    CREATE TABLE group_supervisor_memberships (id serial PRIMARY KEY, group_id integer, supervisor_id integer,
                                               start_date date NOT NULL, end_date date);
    CREATE TABLE operator_schedule_status_periods (
        id serial PRIMARY KEY, operator_id integer NOT NULL, status_code text NOT NULL,
        start_date date NOT NULL, end_date date, comment text, created_by integer,
        exclude_from_tenure boolean NOT NULL DEFAULT FALSE);
    INSERT INTO departments (id, code, name, head_user_id) VALUES
        (1, 'szov', 'СЗоВ', 100), (2, 'op', 'Отдел продаж', 200);
    INSERT INTO directions (id, name) VALUES (7, 'Линия водителей');
    INSERT INTO users (id, name, role, department_id, direction_id, telegram_id) VALUES
        (1, 'Админ Главный', 'super_admin', NULL, NULL, 9001),
        (100, 'Глава Сзовова', 'admin', 1, NULL, 9100),
        (200, 'Глава Продаж', 'sv', 2, NULL, 9200),
        (300, 'Супервайзер Альфа', 'sv', 1, NULL, 9300),
        (301, 'Супервайзер Бета', 'sv', 1, NULL, NULL),
        (302, 'Супервайзер Продаж', 'sv', 2, NULL, NULL),
        (400, 'Оператор Анна', 'operator', 1, 7, 9400),
        (401, 'Оператор Борис', 'operator', 1, 7, NULL),
        (402, 'Оператор Вера', 'operator', 1, 7, NULL),
        (403, 'Оператор Без СВ', 'operator', 1, 7, NULL),
        (404, 'Оператор Продаж', 'operator', 2, NULL, NULL);
    INSERT INTO groups (id, name) VALUES (10, 'Группа 1'), (11, 'Группа 2'), (12, 'Группа без СВ'), (20, 'Группа ОП');
    INSERT INTO group_operator_memberships (group_id, operator_id, start_date) VALUES
        (10, 400, '2026-01-01'), (10, 401, '2026-01-01'), (11, 402, '2026-01-01'),
        (12, 403, '2026-01-01'), (20, 404, '2026-01-01');
    INSERT INTO group_supervisor_memberships (group_id, supervisor_id, start_date) VALUES
        (10, 300, '2026-01-01'), (11, 301, '2026-01-01'), (20, 302, '2026-01-01');
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
            cur.execute("SET statement_timeout TO '20s'")
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


@pytest.fixture()
def db():
    name = 'leave_test_' + uuid.uuid4().hex[:10]
    admin = _Db('public')
    conn = admin.connect()
    with conn.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + name)
    conn.commit()
    conn.close()
    handle = _Db(name)
    with handle.cursor() as cur:
        cur.execute(BASE_DDL)
        schema.init_leave_requests_schema(cur)
    try:
        yield handle
    finally:
        conn = admin.connect()
        with conn.cursor() as cur:
            cur.execute('DROP SCHEMA %s CASCADE' % name)
        conn.commit()
        conn.close()


class Deps:
    """СВ группы и запись периода — как в монолите, но без его схемы."""

    def __init__(self):
        self.periods = []

    def resolve_supervisor(self, cursor, operator_id, day):
        cursor.execute(
            """
            SELECT gsm.supervisor_id
              FROM group_operator_memberships gom
              JOIN group_supervisor_memberships gsm ON gsm.group_id = gom.group_id AND gsm.end_date IS NULL
             WHERE gom.operator_id = %s AND gom.start_date <= %s AND (gom.end_date IS NULL OR gom.end_date >= %s)
             ORDER BY gsm.start_date DESC LIMIT 1
            """,
            (operator_id, day, day),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else None

    def apply_period(self, cursor, *, operator_id, kind, start, end, created_by, exclude_from_tenure, comment):
        cursor.execute(
            """
            INSERT INTO operator_schedule_status_periods
                (operator_id, status_code, start_date, end_date, comment, created_by, exclude_from_tenure)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (operator_id, kind, start, end, comment, created_by, exclude_from_tenure),
        )
        period = {'id': cursor.fetchone()[0], 'kind': kind, 'start': start, 'end': end,
                  'exclude_from_tenure': exclude_from_tenure}
        self.periods.append(period)
        return period


DEPARTMENT_MEMBERS = {1: {400, 401, 402, 403}, 2: {404}}
VIEWERS = {
    ADMIN: {'user_id': ADMIN, 'role': 'super_admin', 'department_id': None, 'is_admin': True,
            'is_supervisor': False, 'headed_department_ids': [], 'is_manager': True},
    HEAD: {'user_id': HEAD, 'role': 'admin', 'department_id': 1, 'is_admin': False,
           'is_supervisor': False, 'headed_department_ids': [1], 'is_manager': True},
    SV_A: {'user_id': SV_A, 'role': 'sv', 'department_id': 1, 'is_admin': False,
           'is_supervisor': True, 'headed_department_ids': [], 'is_manager': True},
    SV_B: {'user_id': SV_B, 'role': 'sv', 'department_id': 1, 'is_admin': False,
           'is_supervisor': True, 'headed_department_ids': [], 'is_manager': True},
    SV_OP: {'user_id': SV_OP, 'role': 'sv', 'department_id': 2, 'is_admin': False,
            'is_supervisor': True, 'headed_department_ids': [], 'is_manager': True},
}


def scope(viewer_id):
    """Периметр «Графиков работы»: админ — все, глава — свой отдел, СВ — свой отдел."""
    viewer = VIEWERS[viewer_id]
    if viewer['is_admin']:
        return set().union(*DEPARTMENT_MEMBERS.values())
    departments = set(viewer['headed_department_ids']) or {viewer['department_id']}
    return set().union(*(DEPARTMENT_MEMBERS.get(d, set()) for d in departments))


def user(cur, user_id):
    return queries.load_user(cur, user_id)


def create(db, deps, operator_id, start, end, kind='annual_leave', reason='Поездка к родителям', outbox=None):
    with db.cursor() as cur:
        return service.create(cur, deps, outbox, user=user(cur, operator_id), today=TODAY,
                              data={'kind': kind, 'start_date': start, 'end_date': end, 'reason': reason})


def decide(db, deps, viewer_id, request_id, outbox=None, **data):
    with db.cursor() as cur:
        return service.decide(cur, deps, outbox, viewer=VIEWERS[viewer_id], actor=user(cur, viewer_id),
                              request_id=request_id, data=data, today=TODAY,
                              in_scope=lambda operator_id: int(operator_id) in scope(viewer_id))


def review(db, viewer_id, status_filter='pending'):
    with db.cursor() as cur:
        return service.review_payload(cur, viewer=VIEWERS[viewer_id], operator_ids=sorted(scope(viewer_id)),
                                      status_filter=status_filter, today=TODAY)


def bell(db, viewer_id, role):
    with db.cursor() as cur:
        return sources.leave_requests(cur, {'user_id': viewer_id, 'role': role}, 10)


# ── Подача ──────────────────────────────────────────────────────────────────

def test_operator_files_request_to_group_supervisor(db):
    deps, outbox = Deps(), notify.Outbox()
    item = create(db, deps, OP_A, '2026-12-01', '2026-12-14', outbox=outbox)
    assert item['status'] == 'supervisor'
    assert item['supervisor'] == {'id': SV_A, 'name': 'Супервайзер Альфа'}
    assert item['days'] == 14
    assert item['actions'] == ['edit', 'cancel']
    assert [e['action'] for e in item['events']] == ['created']
    # Telegram — супервайзеру группы, не оператору и не главе.
    assert [chat for chat, _text, _markup in outbox.messages] == [9300]
    assert 'Оператор Анна' in outbox.messages[0][1] and '14 дней' in outbox.messages[0][1]


def test_without_supervisor_request_goes_straight_to_head(db):
    deps, outbox = Deps(), notify.Outbox()
    item = create(db, deps, OP_NOSV, '2026-12-01', '2026-12-03', outbox=outbox)
    assert item['status'] == 'head'
    assert [chat for chat, _t, _m in outbox.messages] == [9100]


def test_supervisor_step_switched_off_by_department(db):
    with db.cursor() as cur:
        queries.save_settings(cur, 1, {'supervisor_step': False}, ADMIN)
    item = create(db, Deps(), OP_A, '2026-12-01', '2026-12-03')
    assert item['status'] == 'head'


def test_department_without_settings_does_not_accept_requests(db):
    with pytest.raises(LeaveError, match='не принимаются'):
        create(db, Deps(), OP_OP, '2026-12-01', '2026-12-03')


def test_kind_must_be_enabled(db):
    with db.cursor() as cur:
        queries.save_settings(cur, 1, {'allow_bs': False}, ADMIN)
    with pytest.raises(LeaveError, match='вид заявки'):
        create(db, Deps(), OP_A, '2026-12-01', '2026-12-03', kind='bs')


def test_reason_is_required(db):
    with pytest.raises(LeaveError, match='причину'):
        create(db, Deps(), OP_A, '2026-12-01', '2026-12-03', reason='  ')


def test_only_operator_files_requests(db):
    with pytest.raises(LeaveError, match='подаёт оператор'):
        create(db, Deps(), SV_A, '2026-12-01', '2026-12-03')


def test_second_request_on_same_days_is_refused_until_first_is_withdrawn(db):
    deps = Deps()
    first = create(db, deps, OP_A, '2026-12-01', '2026-12-14')
    with pytest.raises(LeaveError, match='уже есть заявка: 01.12 — 14.12'):
        create(db, deps, OP_A, '2026-12-10', '2026-12-20')
    with db.cursor() as cur:
        service.cancel(cur, None, user=user(cur, OP_A), request_id=first['id'], data={}, today=TODAY)
    assert create(db, deps, OP_A, '2026-12-10', '2026-12-20')['status'] == 'supervisor'


def test_schedule_period_already_on_those_days_blocks_request(db):
    with db.cursor() as cur:
        cur.execute("""INSERT INTO operator_schedule_status_periods (operator_id, status_code, start_date, end_date)
                       VALUES (400, 'sick_leave', '2026-12-05', '2026-12-08')""")
    with pytest.raises(LeaveError, match='«Больничный»'):
        create(db, Deps(), OP_A, '2026-12-01', '2026-12-14')


# ── Шаги согласования ───────────────────────────────────────────────────────

def test_full_route_supervisor_then_head_writes_schedule(db):
    deps, outbox = Deps(), notify.Outbox()
    item = create(db, deps, OP_A, '2026-12-01', '2026-12-14')

    after_sv = decide(db, deps, SV_A, item['id'], outbox=outbox, action='approve')
    assert after_sv['status'] == 'head'
    assert after_sv['actions'] == []  # дальше решает глава
    assert sorted(chat for chat, _t, _m in outbox.messages) == [9100, 9400]  # глава и оператор
    assert deps.periods == []

    outbox = notify.Outbox()
    final = decide(db, deps, HEAD, item['id'], outbox=outbox, action='approve', comment='Хорошего отдыха')
    assert final['status'] == 'approved'
    assert final['decidedBy']['id'] == HEAD
    assert final['decisionComment'] == 'Хорошего отдыха'
    assert len(deps.periods) == 1
    period = deps.periods[0]
    assert (period['kind'], period['start'], period['end']) == ('annual_leave', date(2026, 12, 1), date(2026, 12, 14))
    assert period['exclude_from_tenure'] is False
    with db.cursor() as cur:
        cur.execute('SELECT status_period_id FROM leave_requests WHERE id = %s', (item['id'],))
        assert cur.fetchone()[0] == period['id']
    # Оператору — «утверждена», СВ — «руководитель утвердил».
    texts = {chat: text for chat, text, _m in outbox.messages}
    assert set(texts) == {9400, 9300}
    assert 'Заявка утверждена' in texts[9400] and 'Руководитель утвердил' in texts[9300]


def test_supervisor_cannot_approve_on_head_step(db):
    deps = Deps()
    item = create(db, deps, OP_A, '2026-12-01', '2026-12-14')
    decide(db, deps, SV_A, item['id'], action='approve')
    with pytest.raises(LeaveError, match='«Ждёт руководителя»'):
        decide(db, deps, SV_A, item['id'], action='approve')


def test_supervisor_of_another_department_is_out_of_scope(db):
    deps = Deps()
    item = create(db, deps, OP_A, '2026-12-01', '2026-12-14')
    with pytest.raises(LeaveError, match='не в вашем периметре'):
        decide(db, deps, SV_OP, item['id'], action='approve')


def test_any_supervisor_of_department_may_take_first_step(db):
    """Как «Запросы по сменам»: решает периметр отдела, колокол — только прямому СВ."""
    deps = Deps()
    item = create(db, deps, OP_A, '2026-12-01', '2026-12-14')
    assert decide(db, deps, SV_B, item['id'], action='approve')['status'] == 'head'


def test_head_on_supervisor_step_decides_finally(db):
    deps = Deps()
    item = create(db, deps, OP_A, '2026-12-01', '2026-12-14')
    assert decide(db, deps, HEAD, item['id'], action='approve')['status'] == 'approved'
    assert len(deps.periods) == 1


def test_reject_notifies_operator_with_comment(db):
    deps, outbox = Deps(), notify.Outbox()
    item = create(db, deps, OP_A, '2026-12-01', '2026-12-14')
    rejected = decide(db, deps, SV_A, item['id'], outbox=outbox, action='reject', comment='Пик нагрузки')
    assert rejected['status'] == 'rejected'
    assert rejected['decisionComment'] == 'Пик нагрузки'
    assert [chat for chat, _t, _m in outbox.messages] == [9400]
    assert 'Пик нагрузки' in outbox.messages[0][1]
    assert deps.periods == []


# ── Другие даты (ТЗ п. 3) ───────────────────────────────────────────────────

def test_proposal_accepted_moves_dates_and_goes_to_head(db):
    deps = Deps()
    item = create(db, deps, OP_A, '2026-12-01', '2026-12-14')
    proposed = decide(db, deps, SV_A, item['id'], action='propose', start_date='2026-12-15',
                      end_date='2026-12-28', comment='В начале месяца пик')
    assert proposed['status'] == 'proposal'
    assert proposed['proposal']['startDate'] == '2026-12-15'
    assert proposed['proposal']['comment'] == 'В начале месяца пик'
    assert proposed['proposal']['by']['id'] == SV_A

    outbox = notify.Outbox()
    with db.cursor() as cur:
        answered = service.answer_proposal(cur, deps, outbox, user=user(cur, OP_A), request_id=item['id'],
                                           accept=True, data={}, today=TODAY)
    assert answered['status'] == 'head'
    assert (answered['startDate'], answered['endDate']) == ('2026-12-15', '2026-12-28')
    assert answered['proposal'] is None
    assert sorted(chat for chat, _t, _m in outbox.messages) == [9100, 9300]


def test_proposal_declined_returns_to_supervisor_with_own_dates(db):
    deps = Deps()
    item = create(db, deps, OP_A, '2026-12-01', '2026-12-14')
    decide(db, deps, SV_A, item['id'], action='propose', start_date='2026-12-15', end_date='2026-12-28')
    with db.cursor() as cur:
        answered = service.answer_proposal(cur, deps, notify.Outbox(), user=user(cur, OP_A),
                                           request_id=item['id'], accept=False,
                                           data={'comment': 'Билеты уже куплены'}, today=TODAY)
    assert answered['status'] == 'supervisor'
    assert (answered['startDate'], answered['endDate']) == ('2026-12-01', '2026-12-14')
    assert answered['events'][-1]['comment'] == 'Билеты уже куплены'


def test_proposal_with_same_dates_is_refused(db):
    deps = Deps()
    item = create(db, deps, OP_A, '2026-12-01', '2026-12-14')
    with pytest.raises(LeaveError, match='те же даты'):
        decide(db, deps, SV_A, item['id'], action='propose', start_date='2026-12-01', end_date='2026-12-14')


def test_edit_restarts_route_and_drops_proposal(db):
    deps = Deps()
    item = create(db, deps, OP_A, '2026-12-01', '2026-12-14')
    decide(db, deps, SV_A, item['id'], action='propose', start_date='2026-12-15', end_date='2026-12-28')
    with db.cursor() as cur:
        edited = service.edit(cur, deps, notify.Outbox(), user=user(cur, OP_A), request_id=item['id'], today=TODAY,
                              data={'kind': 'annual_leave', 'start_date': '2027-01-05', 'end_date': '2027-01-12',
                                    'reason': 'Перенёс сам'})
    assert edited['status'] == 'supervisor' and edited['proposal'] is None
    assert edited['days'] == 8


def test_approved_request_is_final_for_operator(db):
    deps = Deps()
    item = create(db, deps, OP_A, '2026-12-01', '2026-12-14')
    decide(db, deps, HEAD, item['id'], action='approve')
    with db.cursor() as cur:
        with pytest.raises(LeaveError, match='не отозвать'):
            service.cancel(cur, None, user=user(cur, OP_A), request_id=item['id'], data={}, today=TODAY)


def test_foreign_request_is_not_found_for_operator(db):
    item = create(db, Deps(), OP_A, '2026-12-01', '2026-12-14')
    with db.cursor() as cur:
        with pytest.raises(LeaveError, match='не найдена'):
            service.cancel(cur, None, user=user(cur, OP_B), request_id=item['id'], data={}, today=TODAY)


# ── Стаж (ТЗ п. 8) ──────────────────────────────────────────────────────────

def test_long_bs_is_excluded_from_tenure_by_default_and_head_can_override(db):
    deps = Deps()
    long_bs = create(db, deps, OP_A, '2026-12-01', '2027-01-10', kind='bs', reason='Уход за родственником')
    queue = {it['id']: it for it in review(db, HEAD)['requests']}
    assert queue[long_bs['id']]['tenureDefault'] is True
    assert queue[long_bs['id']]['longBsDays'] == 30
    decide(db, deps, HEAD, long_bs['id'], action='approve')
    assert deps.periods[-1]['exclude_from_tenure'] is True

    short_bs = create(db, deps, OP_B, '2026-12-01', '2027-01-10', kind='bs')
    decide(db, deps, HEAD, short_bs['id'], action='approve', exclude_from_tenure=False)
    assert deps.periods[-1]['exclude_from_tenure'] is False

    vacation = create(db, deps, OP_C, '2026-12-01', '2027-01-10')
    decide(db, deps, HEAD, vacation['id'], action='approve', exclude_from_tenure=True)
    assert deps.periods[-1]['exclude_from_tenure'] is False  # отпуск всегда в стаже


# ── Пересечения (ТЗ п. 4) ───────────────────────────────────────────────────

def test_overlaps_show_only_approved_absences_of_the_department(db):
    deps = Deps()
    # Ожидающая заявка коллеги пересечением не считается (ТЗ: «только утверждённые»).
    create(db, deps, OP_NOSV, '2026-12-01', '2026-12-14')
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO operator_schedule_status_periods (operator_id, status_code, start_date, end_date) VALUES
                (402, 'annual_leave', '2026-12-10', '2026-12-20'),  -- другая группа отдела
                (401, 'bs', '2026-12-12', '2026-12-13'),            -- своя группа
                (404, 'annual_leave', '2026-12-01', '2026-12-31'),  -- другой отдел
                (403, 'sick_leave', '2026-12-05', '2026-12-06')     -- не отпуск
        """)
    mine = create(db, deps, OP_A, '2026-12-01', '2026-12-14')
    item = next(it for it in review(db, SV_A)['requests'] if it['id'] == mine['id'])
    assert item['overlaps']['total'] == 2
    names = [o['name'] for o in item['overlaps']['items']]
    assert names == ['Оператор Борис', 'Оператор Вера']  # своя группа — первой
    assert item['overlaps']['items'][0]['sameGroup'] is True


def test_review_counts_only_requests_waiting_for_this_person(db):
    deps = Deps()
    first = create(db, deps, OP_A, '2026-12-01', '2026-12-05')
    second = create(db, deps, OP_C, '2026-12-01', '2026-12-05')
    decide(db, deps, SV_B, second['id'], action='approve')
    assert review(db, SV_A)['pendingCount'] == 1    # шаг СВ: заявка Анны
    assert review(db, HEAD)['pendingCount'] == 1    # шаг главы: заявка Веры
    assert review(db, SV_OP)['requests'] == []      # чужой отдел
    assert {it['id'] for it in review(db, HEAD)['requests']} == {first['id'], second['id']}
    head_view = {it['id']: it for it in review(db, HEAD)['requests']}
    assert head_view[first['id']]['approveIsFinal'] is True
    sv_view = {it['id']: it for it in review(db, SV_A)['requests']}
    assert sv_view[first['id']]['approveIsFinal'] is False


# ── Колокол ─────────────────────────────────────────────────────────────────

def test_bell_follows_whose_turn_it_is(db):
    deps = Deps()
    item = create(db, deps, OP_A, '2026-12-01', '2026-12-14')
    assert bell(db, SV_A, 'sv')[0] == 1
    assert bell(db, SV_B, 'sv')[0] == 0      # не его группа
    assert bell(db, HEAD, 'admin')[0] == 0   # ещё не его шаг
    decide(db, deps, SV_A, item['id'], action='approve')
    assert bell(db, SV_A, 'sv')[0] == 0
    total, items = bell(db, HEAD, 'admin')
    assert total == 1 and items[0]['target'] == 'leave:%d' % item['id']
    assert items[0]['body'] == 'Отпуск · 01.12 — 14.12'


def test_bell_tells_operator_about_proposal_and_decision_until_seen(db):
    deps = Deps()
    item = create(db, deps, OP_A, '2026-12-01', '2026-12-14')
    assert bell(db, OP_A, 'operator')[0] == 0
    decide(db, deps, SV_A, item['id'], action='propose', start_date='2026-12-15', end_date='2026-12-28')
    total, items = bell(db, OP_A, 'operator')
    assert total == 1 and items[0]['title'] == 'Предложены другие даты'
    assert items[0]['body'] == 'Отпуск · 15.12 — 28.12'
    with db.cursor() as cur:
        service.answer_proposal(cur, deps, None, user=user(cur, OP_A), request_id=item['id'], accept=True,
                                data={}, today=TODAY)
    decide(db, deps, HEAD, item['id'], action='approve')
    total, items = bell(db, OP_A, 'operator')
    assert total == 1 and items[0]['title'] == 'Заявка утверждена'
    with db.cursor() as cur:
        assert service.mark_seen(cur, user(cur, OP_A)) == 1
    assert bell(db, OP_A, 'operator')[0] == 0


def test_operator_payload_counts_what_needs_attention(db):
    deps = Deps()
    item = create(db, deps, OP_A, '2026-12-01', '2026-12-14')
    decide(db, deps, SV_A, item['id'], action='propose', start_date='2026-12-15', end_date='2026-12-28')
    with db.cursor() as cur:
        payload = service.operator_payload(cur, user(cur, OP_A), TODAY)
    assert payload['enabled'] is True
    assert payload['kinds'] == ['annual_leave', 'bs']
    assert payload['attentionCount'] == 1
    assert payload['requests'][0]['actions'] == ['accept', 'decline', 'edit', 'cancel']


# ── Стаж в монолите: дни «не в стаж» ────────────────────────────────────────

def _database_method(name):
    # Database собран из миксинов db/*.py — метод ищем в склейке, как остальные тесты.
    from tests import source_cache
    source = source_cache.database_flat_text()
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Database')
    node = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    lines = source.splitlines()[node.lineno - 1:node.end_lineno]
    segment = textwrap.dedent('\n'.join(line for line in lines if not line.strip().startswith('@staticmethod')))
    namespace = {'date': date, 'datetime': datetime, 'timedelta': timedelta}
    exec(segment, namespace)  # noqa: S102 — свой исходник, не ввод пользователя
    return namespace[name]


def test_tenure_excluded_days_count_only_flagged_and_passed_days(db):
    excluded_days = _database_method('_tenure_excluded_days_tx')
    shift = _database_method('_shift_hire_date_by_excluded_days')
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO operator_schedule_status_periods (operator_id, status_code, start_date, end_date, exclude_from_tenure)
            VALUES (400, 'bs', '2026-03-01', '2026-03-31', TRUE),
                   (400, 'bs', '2026-10-20', '2026-11-30', TRUE),   -- идёт: считается прошедшая часть
                   (400, 'bs', '2026-12-01', '2026-12-31', TRUE),   -- ещё не началось
                   (400, 'annual_leave', '2026-06-01', '2026-06-14', FALSE),
                   (401, 'bs', '2026-01-01', '2026-01-10', FALSE)
        """)
        days = excluded_days(None, cur, [400, 401], TODAY)
    assert days == {400: 31 + 14}
    assert shift(date(2025, 1, 1), 45) == date(2025, 2, 15)
    assert shift(date(2025, 1, 1), 0) == date(2025, 1, 1)
    assert shift(None, 45) is None
