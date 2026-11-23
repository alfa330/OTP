# -*- coding: utf-8 -*-
"""Журнал робота пропущенных на настоящем Postgres — переадресация в Syntony (SALES-4).

CDR_MISSED_TEST_PORT — порт локального кластера. Логику цикла без базы проверяет
tests/test_cdr_missed_service.py; здесь — настоящие DDL и запросы cdr/missed_queries.py и
cdr/queries.py:
  * на боевой базе таблица журнала уже есть — колонки crm и syntony_sent_at добавляются
    миграцией, старые строки остаются amoCRM;
  * заявка, принятая Syntony, — `created` с отметкой времени и без номера сделки;
  * цепочка перезвонов получает отметку Syntony, и «дозвонился» её не перетирает;
  * строка, упавшая в amoCRM до переезда, берётся на повтор уже как Syntony;
  * «Касания»: метка, фильтр «переданные в CRM» и итоги по обеим CRM.

Ни боевых учёток, ни сети: только 127.0.0.1, своя схема со случайным именем, в конце она
удаляется. Номера выдуманы.

    CDR_MISSED_TEST_PORT=55491 python -m pytest tests/test_cdr_missed_syntony_postgres.py
"""

import os
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PORT = os.environ.get('CDR_MISSED_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (CDR_MISSED_TEST_PORT)')

DAY = date(2026, 11, 23)
T0 = datetime(2026, 11, 23, 10, 0, 0)
PHONE = '7015550001'

# Журнал в том виде, в каком он живёт на боевой базе до SALES-4 (задача #291).
OLD_JOURNAL = """
CREATE TABLE cdr_missed_leads (
    linkedid       VARCHAR(64) NOT NULL,
    phone          VARCHAR(16) NOT NULL,
    call_day       DATE        NOT NULL,
    started_at     TIMESTAMP   NOT NULL,
    ended_at       TIMESTAMP,
    status         VARCHAR(16) NOT NULL,
    reason         TEXT,
    next_linkedid  VARCHAR(64),
    amo_lead_id    BIGINT,
    amo_contact_id BIGINT,
    attempts       INTEGER     NOT NULL DEFAULT 0,
    error          TEXT,
    decided_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at        TIMESTAMPTZ,
    PRIMARY KEY (linkedid, phone)
);
"""


@pytest.fixture()
def cursor():
    import psycopg2
    from cdr import schema

    name = 'cdr_missed_%s' % uuid.uuid4().hex[:10]
    conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres')
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute('CREATE SCHEMA %s' % name)
    cur.execute('SET search_path TO %s' % name)
    cur.execute("SET client_encoding TO 'UTF8'")
    cur.execute('CREATE TABLE test_phone_numbers (phone_key TEXT PRIMARY KEY)')
    cur.execute(OLD_JOURNAL)
    cur.execute("""
        INSERT INTO cdr_missed_leads (linkedid, phone, call_day, started_at, status, amo_lead_id)
        VALUES ('old.1', '7015550009', '2026-11-20', '2026-11-20 09:00:00', 'created', 57000001)
    """)
    schema.init_cdr_schema(cur)
    try:
        yield cur
    finally:
        cur.execute('DROP SCHEMA %s CASCADE' % name)
        conn.close()


def touch(linkedid, started, phone=PHONE, call_type='Входящий (не приняли)', result='Занято',
          queue='3010'):
    return {'linkedid': linkedid, 'phone': phone, 'call_day': started.date(), 'started_at': started,
            'call_type': call_type, 'result': result, 'queue': queue}


def add_touch(cursor, item):
    cursor.execute("""
        INSERT INTO cdr_touches (linkedid, phone, call_day, started_at, call_type, result, queue,
                                 line_number)
        VALUES (%(linkedid)s, %(phone)s, %(call_day)s, %(started_at)s, %(call_type)s, %(result)s,
                %(queue)s, '7475550078')
    """, item)


def journal(cursor, linkedid, phone=PHONE):
    cursor.execute("""
        SELECT status, crm, syntony_sent_at IS NOT NULL, amo_lead_id, attempts, error
          FROM cdr_missed_leads WHERE linkedid = %s AND phone = %s
    """, (linkedid, phone))
    return cursor.fetchone()


def test_migration_adds_columns_to_the_existing_journal(cursor):
    from cdr import schema

    cursor.execute("""
        SELECT column_name, data_type FROM information_schema.columns
         WHERE table_schema = current_schema() AND table_name = 'cdr_missed_leads'
           AND column_name IN ('crm', 'syntony_sent_at') ORDER BY column_name
    """)
    assert cursor.fetchall() == [('crm', 'character varying'),
                                 ('syntony_sent_at', 'timestamp with time zone')]
    # Строка до переезда — amoCRM: CRM не названа, сделка на месте.
    assert journal(cursor, 'old.1', '7015550009') == ('created', None, False, 57000001, 0, None)
    schema.init_cdr_schema(cursor)       # второй старт — без ошибок


def test_syntony_accepted_row(cursor):
    from cdr import missed_queries as mq

    assert mq.insert_decision(cursor, touch('1.1', T0), mq.SENDING, 'За минуту…', crm='syntony')
    assert journal(cursor, '1.1') == ('sending', 'syntony', False, None, 1, None)
    mq.mark_sent_syntony(cursor, '1.1', PHONE)
    assert journal(cursor, '1.1') == ('created', 'syntony', True, None, 1, None)
    # Повторное решение по тому же звонку не заводится.
    assert not mq.insert_decision(cursor, touch('1.1', T0), mq.SENDING, 'x', crm='syntony')
    with pytest.raises(ValueError):
        mq.insert_decision(cursor, touch('1.2', T0), mq.SENDING, 'x', crm='bitrix')


def test_chain_gets_the_syntony_mark_and_keeps_it(cursor):
    from cdr import missed_queries as mq

    mq.insert_decision(cursor, touch('1.1', T0), mq.CHAINED, 'перезвонил', crm='syntony')
    mq.insert_decision(cursor, touch('1.2', T0 + timedelta(minutes=1)), mq.SENDING, 'x', crm='syntony')
    mq.mark_sent_syntony(cursor, '1.2', PHONE)
    mq.resolve_chain(cursor, PHONE, T0 + timedelta(minutes=1), syntony=True)
    assert journal(cursor, '1.1')[:3] == ('chained', 'syntony', True)
    # Позднее «дозвонился» по другой цепочке того же номера не перетирает отметку.
    mq.resolve_chain(cursor, PHONE, T0 + timedelta(minutes=30), answered_reason='Клиент перезвонил')
    assert journal(cursor, '1.1')[:3] == ('chained', 'syntony', True)


def test_answered_chain_without_a_mark_is_closed(cursor):
    from cdr import missed_queries as mq

    mq.insert_decision(cursor, touch('1.1', T0), mq.CHAINED, 'перезвонил', crm='syntony')
    mq.resolve_chain(cursor, PHONE, T0 + timedelta(minutes=2), answered_reason='Клиент дозвонился')
    assert journal(cursor, '1.1')[:3] == ('answered', 'syntony', False)


def test_amo_row_failed_before_the_switch_is_reclaimed_as_syntony(cursor):
    from cdr import missed_queries as mq

    mq.insert_decision(cursor, touch('1.1', T0), mq.ERROR, 'x', error='amoCRM 502', crm='amo')
    assert mq.reclaim(cursor, '1.1', PHONE, 'error', 0, crm='syntony') == 1
    assert journal(cursor, '1.1')[:2] == ('sending', 'syntony')
    # Без crm повтор CRM строки не трогает.
    mq.mark_error(cursor, '1.1', PHONE, 'HTTP 503')
    assert mq.reclaim(cursor, '1.1', PHONE, 'error', 1) == 2
    assert journal(cursor, '1.1')[:2] == ('sending', 'syntony')


def test_retry_queue_sees_syntony_rows(cursor):
    from cdr import missed_queries as mq

    add_touch(cursor, touch('1.1', T0))
    mq.insert_decision(cursor, touch('1.1', T0), mq.ERROR, 'x', error='Syntony недоступна',
                       crm='syntony')
    rows = mq.due_retries(cursor)
    assert [(r['linkedid'], r['status'], r['queue']) for r in rows] == [('1.1', 'error', '3010')]


def test_touches_show_both_crms_and_count_them_apart(cursor):
    from cdr import missed_queries as mq, queries

    for linkedid, minute in (('1.1', 0), ('1.2', 10), ('1.3', 20), ('1.4', 30)):
        add_touch(cursor, touch(linkedid, T0 + timedelta(minutes=minute)))
    mq.insert_decision(cursor, touch('1.1', T0), mq.SENDING, 'За минуту…', crm='syntony')
    mq.mark_sent_syntony(cursor, '1.1', PHONE)
    mq.insert_decision(cursor, touch('1.2', T0 + timedelta(minutes=10)), mq.SENDING, 'x', crm='amo')
    mq.mark_created(cursor, '1.2', PHONE, 57009999)
    mq.insert_decision(cursor, touch('1.3', T0 + timedelta(minutes=20)), mq.ANSWERED,
                       'Клиент перезвонил', crm='syntony')
    mq.insert_decision(cursor, touch('1.4', T0 + timedelta(minutes=30)), mq.SENDING, 'x', crm='syntony')
    mq.mark_error(cursor, '1.4', PHONE, 'форма Syntony не найдена (HTTP 404 unknown_form)', final=True)

    rows = {t['linkedid']: t for t in queries.select_touches(cursor, DAY, DAY)}
    assert (rows['1.1']['amo_state'], rows['1.1']['missed_crm'], rows['1.1']['amo_url']) == \
        ('sent', 'syntony', '')
    assert (rows['1.2']['amo_state'], rows['1.2']['missed_crm'], rows['1.2']['amo_lead_id']) == \
        ('sent', 'amo', 57009999)
    assert (rows['1.3']['amo_state'], rows['1.3']['missed_crm']) == ('answered', 'syntony')
    assert (rows['1.4']['amo_state'], rows['1.4']['missed_crm']) == ('error', 'syntony')
    assert 'HTTP 404' in rows['1.4']['amo_note']

    only = queries.select_touches(cursor, DAY, DAY, {'amo_only': True})
    assert sorted(t['linkedid'] for t in only) == ['1.1', '1.2']
    assert queries.count_touches(cursor, DAY, DAY, {'amo_only': True}) == 2

    summary = queries.summary(cursor, DAY, DAY)
    assert (summary['incoming_missed'], summary['amo_transferred'],
            summary['syntony_transferred']) == (4, 1, 1)
    exported = list(queries.iter_touches(cursor, DAY, DAY))
    assert len(exported) == 4
