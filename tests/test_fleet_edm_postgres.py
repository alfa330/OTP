# -*- coding: utf-8 -*-
"""«Провайдер ЭДО» на настоящем Postgres: счёт залипаний выгрузки — orphan_jobs и claim_job.

Opt-in, как соседние тесты схемы: OTP_SCHEMA_LOCK_TEST_PORT — порт одноразового локального кластера
(initdb -A trust). Тест заводит свою схему, ставит в неё схему раздела (FLEET_EDM_SCHEMA_SQL) и удаляет её.

Правило (27.11.2026, выгрузка №65 на 244 616 строк — сутки карточек одной учёткой, 12 подхватов за 7,5 часа
выкладками прода): закрываем «слишком много перезапусков» за 40 попыток ПОДРЯД, умерших быстрее
LONG_ATTEMPT_SECONDS, а не за 40 подхватов всего; общий предел — страховка от долгого клинча.
"""
import os
import uuid

import pytest

psycopg2 = pytest.importorskip('psycopg2')
from psycopg2 import sql as pg_sql  # noqa: E402

from fleet_edm import queries  # noqa: E402
from fleet_edm.schema import FLEET_EDM_SCHEMA_SQL  # noqa: E402

PORT = os.environ.get('OTP_SCHEMA_LOCK_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='нужен OTP_SCHEMA_LOCK_TEST_PORT — одноразовый Postgres')

ME = 'portal-api-me'
DEAD = 'portal-api-dead'


def _connect(schema=None):
    options = '-c statement_timeout=10000' + (' -c search_path=' + schema if schema else '')
    return psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', password='', dbname='postgres',
                            connect_timeout=3, application_name='otp-fleet-edm-test', options=options)


@pytest.fixture
def cur():
    schema = 'otp_edm_' + uuid.uuid4().hex[:10]
    admin = _connect()
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute(pg_sql.SQL('CREATE SCHEMA {}').format(pg_sql.Identifier(schema)))
    conn = _connect(schema)
    conn.autocommit = True
    cursor = conn.cursor()
    cursor.execute("SET TIME ZONE 'UTC'")
    cursor.execute(FLEET_EDM_SCHEMA_SQL)
    try:
        yield cursor
    finally:
        conn.close()
        with admin.cursor() as c:
            c.execute(pg_sql.SQL('DROP SCHEMA {} CASCADE').format(pg_sql.Identifier(schema)))
        admin.close()


def _job(cur, *, attempts, stalls, lived_min, silent_sec=120, owner=DEAD):
    """Идущая выгрузка, чья последняя попытка прожила lived_min минут и молчит silent_sec секунд."""
    cur.execute("""
        INSERT INTO fleet_edm_jobs (status, started_at, attempts, stalls, attempt_started_at, progress_at,
                                    owner_instance)
        VALUES ('running', NOW() - interval '1 day', %(attempts)s, %(stalls)s,
                NOW() - make_interval(secs => %(silent)s) - make_interval(mins => %(lived)s),
                NOW() - make_interval(secs => %(silent)s), %(owner)s)
        RETURNING id
    """, {'attempts': attempts, 'stalls': stalls, 'lived': lived_min, 'silent': silent_sec, 'owner': owner})
    return cur.fetchone()[0]


def _row(cur, job_id):
    cur.execute("""SELECT status, error_code, attempts, stalls, NOW() - attempt_started_at < interval '5 seconds'
                   FROM fleet_edm_jobs WHERE id = %s""", (job_id,))
    return cur.fetchone()


def test_long_attempt_killed_by_a_deploy_is_resumed_and_resets_the_count(cur):
    # 39 быстрых смертей подряд, но последняя попытка прожила 40 минут — её убила выкладка.
    job = _job(cur, attempts=60, stalls=39, lived_min=40)
    outcome = queries.orphan_jobs(cur, ME)
    assert outcome == {'resume': [job], 'exhausted': []}
    assert queries.claim_job(cur, job, ME) == 61
    status, code, attempts, stalls, fresh_start = _row(cur, job)
    assert (status, code, attempts, stalls, fresh_start) == ('running', None, 61, 0, True)


def test_fortieth_quick_death_in_a_row_closes_the_job(cur):
    job = _job(cur, attempts=39, stalls=39, lived_min=2)
    outcome = queries.orphan_jobs(cur, ME)
    assert outcome == {'resume': [], 'exhausted': [job]}
    assert _row(cur, job)[:2] == ('error', 'too_many_restarts')


def test_quick_death_counts_up_on_claim(cur):
    job = _job(cur, attempts=5, stalls=3, lived_min=2)
    assert queries.orphan_jobs(cur, ME)['resume'] == [job]
    assert queries.claim_job(cur, job, ME) == 6
    assert _row(cur, job)[3] == 4


def test_total_attempts_are_still_a_backstop(cur):
    job = _job(cur, attempts=150, stalls=0, lived_min=40)
    assert queries.orphan_jobs(cur, ME) == {'resume': [], 'exhausted': [job]}


def test_live_job_is_left_alone(cur):
    job = _job(cur, attempts=3, stalls=0, lived_min=40, silent_sec=10)
    assert queries.orphan_jobs(cur, ME) == {'resume': [], 'exhausted': []}
    assert _row(cur, job)[0] == 'running'


COOKIES = [{'name': 'Session_id', 'value': 'x'}]


def test_extra_account_is_one_row_per_login_and_relogin_clears_the_error(cur):
    queries.save_extra_session(cur, account='a@', cookies=COOKIES, user_agent='ua', parks_count=80, updated_by=2)
    queries.mark_extra_session(cur, 'a@', error='Кабинет Fleet не принял сессию (401).')
    queries.save_extra_session(cur, account='a@', cookies=[{'name': 'Session_id', 'value': 'y'}],
                               parks_count=89)
    rows = queries.extra_status(cur)
    assert [(r['account'], r['parks_count'], r['last_error']) for r in rows] == [('a@', 89, None)]
    assert 'cookies' not in rows[0] and 'user_agent' not in rows[0]
    with_cookies = queries.extra_status(cur, with_cookies=True)
    assert with_cookies[0]['cookies'] == [{'name': 'Session_id', 'value': 'y'}]


def test_helpers_are_mailings_then_live_extra_accounts(cur):
    cur.execute("""CREATE TABLE driver_mailing_session (id INTEGER PRIMARY KEY DEFAULT 1, cookies TEXT NOT NULL,
                                                         user_agent TEXT, account TEXT)""")
    cur.execute("INSERT INTO driver_mailing_session (cookies, account) VALUES (%s, 'ooz@')",
                ('[{"name": "Session_id", "value": "m"}]',))
    for account in ('b@', 'a@', 'dead@'):
        queries.save_extra_session(cur, account=account, cookies=COOKIES, parks_count=89)
    queries.mark_extra_session(cur, 'dead@', error='401')
    helpers = queries.load_helper_sessions(cur)
    assert [(h['kind'], h['account']) for h in helpers] == [('mailings', 'ooz@'), ('extra', 'a@'),
                                                           ('extra', 'b@')]
    assert helpers[1]['cookies'] == COOKIES


def test_alive_mark_refreshes_parks_and_removal_works(cur):
    queries.save_extra_session(cur, account='a@', cookies=COOKIES, parks_count=50)
    queries.mark_extra_session(cur, 'a@', parks_count=89)
    assert queries.extra_status(cur)[0]['parks_count'] == 89
    queries.mark_extra_session(cur, 'a@')                     # без числа — прежнее остаётся
    assert queries.extra_status(cur)[0]['parks_count'] == 89
    assert queries.delete_extra_session(cur, 'a@') is True
    assert queries.delete_extra_session(cur, 'a@') is False
    assert queries.extra_status(cur) == []


def test_old_rows_without_attempt_start_count_from_the_job_start(cur):
    """Строки до правки: attempt_started_at пуст — попытка считается от начала выгрузки (так у №65:
    13 подхватов, последний жил 16 часов) и залипанием не считается."""
    cur.execute("""
        INSERT INTO fleet_edm_jobs (status, started_at, attempts, progress_at, owner_instance)
        VALUES ('running', NOW() - interval '1 day', 14, NOW() - interval '2 minutes', %s) RETURNING id
    """, (DEAD,))
    job = cur.fetchone()[0]
    assert queries.orphan_jobs(cur, ME)['resume'] == [job]
    assert queries.claim_job(cur, job, ME) == 15
    assert _row(cur, job)[3] == 0
