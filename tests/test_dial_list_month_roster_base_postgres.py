# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: DIAL_LIST_TEST_PORT — порт локального Postgres.

База месяца «Удаленного КЦ» (владелец, 10.11.2026: после исключения водителей не из списка Sapar «Аналитика» всё
ещё показывала 44 тыс. «в базе»): исключённый водитель, которого нет в списке месяца (dial_list_report_roster,
«Города по ИИН»), в базу месяца не входит — ни в «Аналитику», ни в сводку и выбор месяца, ни в «в базе» отдела,
ни в журнал, ни в проверку подписания (плановую и перед отчётом). Исключённый ИЗ списка (октябрь: «передан на
внешний обзвон»), водитель не из списка, но не исключённый (решение 21.10: «в обзвоне, но не в отчёте»), и строка
«только для отчёта» — в базе. У месяца без списка база — все строки, как раньше.

Ни боевых учёток, ни сети: только 127.0.0.1, своя схема со случайным именем, в конце удаляется; Sapar подменён.
Запуск на локальном кластере:

    DIAL_LIST_TEST_PORT=55461 python -m pytest tests/test_dial_list_month_roster_base_postgres.py

Люди, номера и ИИН выдуманы.
"""
import contextlib
import os
import sys
import uuid
from datetime import datetime, time, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import analytics as dial_analytics  # noqa: E402
from dial_list import month_base  # noqa: E402
from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402
from dial_list.sign_check import SignChecker  # noqa: E402

PORT = os.environ.get('DIAL_LIST_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (DIAL_LIST_TEST_PORT)')

RCC = 1954
UTC = timezone.utc

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, name text NOT NULL, code text,
                              is_active boolean DEFAULT TRUE);
    CREATE TABLE test_phone_numbers (phone_key varchar(10) UNIQUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, login text, role text,
                        status text NOT NULL DEFAULT 'working', department_id integer,
                        sip_number text);
    CREATE TABLE sip_department_config (department_id integer PRIMARY KEY, provider text,
                                        sip_server text, ring_timeout_rings smallint);
    INSERT INTO departments (id, name, code) VALUES (1954, 'Удаленный КЦ', 'remote_cc');
    INSERT INTO sip_department_config VALUES (1954, 'binotel', 'sip53.binotel.com', NULL);
'''


class _Db:
    _SIP_INACTIVE_STATUSES = ('fired', 'dismissal')

    def __init__(self, schema):
        self.schema = schema

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                                connect_timeout=3)
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


class _NoSapar:
    def configured(self):
        return False


# Водители месяца со списком: (метка, номер, ИИН, статус, подписал, в списке месяца).
CURRENT = [
    ('в списке', '77010000001', '900101300001', 'new', False, True),
    ('в списке, исключён — внешний обзвон', '77010000002', '900101300002', 'excluded', False, True),
    ('не в списке, в обзвоне — решение 21.10', '77010000003', '900101300003', 'new', False, False),
    ('не в списке, исключён', '77010000004', '900101300004', 'excluded', False, False),
    ('не в списке, исключён, подписал', '77010000005', '900101300005', 'excluded', True, False),
]
REPORT_ONLY_IIN = '900101300006'      # строка «только для отчёта»: без номера, исключена, в списке
HIDDEN = {'900101300004', '900101300005'}


class _Env:
    def __init__(self, db, svc):
        self.db, self.svc = db, svc
        self.now = dial_service.current_period()
        self.prev = (self.now - timedelta(days=1)).replace(day=1)
        self.ids = {}

    def q(self, sql, params=None):
        with self.db._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    def lead(self, phone, iin, period, status='new', signed=False, name='Водитель', note=''):
        row = self.q("""
            INSERT INTO dial_list_leads (department_id, phone_norm, full_name, period, iin, status, note,
                                         sign_status, signed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (RCC, phone, name, period, iin, status, note, 'signed' if signed else '',
              datetime(2026, 11, 3, 6, 0, tzinfo=UTC) if signed else None))
        self.ids[iin] = str(row[0][0])
        return self.ids[iin]

    def roster(self, period, iins):
        for iin in iins:
            self.q("INSERT INTO dial_list_report_roster (department_id, period, iin, city) VALUES (%s, %s, %s, %s)",
                   (RCC, period, iin, 'Алматы'))

    def visible(self, period):
        return {r[0] for r in self.q("SELECT l.iin FROM dial_list_leads l WHERE l.department_id = %s AND l.period = %s"
                                     " AND " + month_base.in_month_base_sql('l'), (RCC, period))}


@pytest.fixture
def env():
    schema = 't_dial_roster_base_' + uuid.uuid4().hex
    db = _Db(schema)
    admin = db.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        dial_schema.init_dial_list_schema(cur)
        cur.execute("INSERT INTO dial_list_department_settings (department_id, enabled) VALUES (1954, TRUE)")
    svc = dial_service.DialListService(db, client_factory=lambda dep: None, sign_checker=_NoSapar(),
                                       calls_submit=lambda fn: fn())
    e = _Env(db, svc)
    for _label, phone, iin, status, signed, _in_list in CURRENT:
        e.lead(phone, iin, e.now, status=status, signed=signed)
    e.lead(dial_service.report_only_phone(REPORT_ONLY_IIN), REPORT_ONLY_IIN, e.now, status='excluded',
           note=dial_service.REPORT_ONLY_NOTE)
    e.roster(e.now, [iin for _l, _p, iin, _s, _g, in_list in CURRENT if in_list] + [REPORT_ONLY_IIN])
    # Прошлый месяц — без списка: исключённый там остаётся в базе.
    e.lead('77010000011', '900101300011', e.prev)
    e.lead('77010000012', '900101300012', e.prev, status='excluded')
    try:
        yield e
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


def _all_current():
    return {iin for _l, _p, iin, _s, _g, _in in CURRENT} | {REPORT_ONLY_IIN}


def test_rule_hides_only_excluded_outside_the_month_list(env):
    assert env.visible(env.now) == _all_current() - HIDDEN
    # Месяц без списка — все строки, исключённая тоже.
    assert env.visible(env.prev) == {'900101300011', '900101300012'}


def test_analytics_counts_the_month_base(env):
    with env.db._get_cursor() as cur:
        data = dial_analytics.department_analytics(cur, RCC, env.now, today=env.now + timedelta(days=9),
                                                   configured=False)
    totals = data['totals']
    assert totals['leads'] == 4                 # было бы 6: двое исключённых не из списка не считаются
    assert totals['with_iin'] == 4
    assert totals['excluded'] == 2              # «внешний обзвон» и строка «только для отчёта»
    assert totals['signed'] == 0                # подписавший не из списка — тоже не в базе
    assert sum(d['signed'] for d in data['by_day']) == 0


def test_summary_and_month_picker_count_the_month_base(env):
    summary = env.svc.leads_summary(RCC, env.now)
    assert summary['total'] == 4
    assert summary['by_status']['excluded'] == 2 and summary['signed'] == 0
    totals = {p['period']: p['total'] for p in summary['periods']}
    assert totals == {env.now.isoformat(): 4, env.prev.isoformat(): 2}
    # «в базе» отдела — по всем месяцам, по тому же правилу.
    assert env.svc.list_departments([RCC])[0]['leads_total'] == 6


def test_journal_lists_the_month_base_and_card_still_opens(env):
    page = env.svc.leads_journal(RCC, period=env.now, limit=50)
    assert page['total'] == 4
    shown = {item['id'] for item in page['items']}
    assert shown == {env.ids[i] for i in _all_current() - HIDDEN}
    # Поиск по хвосту номера скрытого водителя его не находит.
    assert env.svc.leads_journal(RCC, period=env.now, q='0004')['total'] == 0
    # Этап «Исключены»: только те, кто в базе месяца.
    excluded = env.svc.leads_journal(RCC, period=env.now, stage='excluded')
    assert {item['id'] for item in excluded['items']} == {env.ids['900101300002'], env.ids[REPORT_ONLY_IIN]}
    # «Все месяцы» — правило по месяцу каждой строки.
    assert env.svc.leads_journal(RCC, period='all')['total'] == 6
    # Карточку скрытого водителя по ссылке открыть можно: история не потеряна.
    card = env.svc.lead_card(env.ids['900101300004'])
    assert card['id'] == env.ids['900101300004']


def _checker(env, asked):
    def fetch(iin, month, year, session=None, timeout=None):
        asked.append(iin)
        return {'ok': True, 'yandex': [{'ServiceId': 1, 'Status': 'НаПодписанииУВодителя'}], 'park': []}

    def fetch_ids(month, year, wanted=None):
        return {'ok': True, 'ids': {}, 'listed': 1}

    return SignChecker(env.db, fetch=fetch, configured=lambda: True, sleep=lambda _s: None, fetch_ids=fetch_ids)


def test_sign_check_run_and_refresh_skip_excluded_outside_the_list(env):
    now = datetime.combine(env.now + timedelta(days=9), time(5, 0), tzinfo=UTC)
    asked = []
    _checker(env, asked).run(reason='тест', now=now)
    expected = (_all_current() - HIDDEN) | {'900101300011', '900101300012'}
    assert set(asked) == expected                 # прошлый месяц без списка — проверяется весь
    # Перед отчётом — только база месяца отчёта.
    asked.clear()
    _checker(env, asked).refresh([(RCC, env.now)], now + timedelta(hours=1), now + timedelta(hours=2),
                                 reason='тест', now=now + timedelta(minutes=30))
    assert set(asked) == _all_current() - HIDDEN
    # «Проверить сейчас» по самому водителю — и для скрытого.
    asked.clear()
    _checker(env, asked).run(reason='карточка', now=now, lead_ids=[env.ids['900101300004']], force=True)
    assert asked == ['900101300004']


def test_without_month_list_everything_is_the_base_again(env):
    env.q("DELETE FROM dial_list_report_roster WHERE department_id = %s AND period = %s", (RCC, env.now))
    assert env.visible(env.now) == _all_current()
    with env.db._get_cursor() as cur:
        data = dial_analytics.department_analytics(cur, RCC, env.now, today=env.now + timedelta(days=9),
                                                   configured=False)
    assert data['totals']['leads'] == 6
    assert env.svc.leads_summary(RCC, env.now)['total'] == 6
    assert env.svc.leads_journal(RCC, period=env.now)['total'] == 6
