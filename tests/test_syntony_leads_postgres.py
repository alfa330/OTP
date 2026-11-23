# -*- coding: utf-8 -*-
"""Заявки Syntony и журнал робота OLX на настоящем Postgres: SYNTONY_LEADS_TEST_PORT — порт
локального кластера.

Логику без базы проверяет tests/test_syntony_leads.py. Здесь — настоящие DDL и запросы:
таблицы отбивки, запись заявок без холостых UPDATE, выборки по окну, журнал выгрузок, исход
`received` в журнале робота OLX (повтор за день, сводка, отбивка, «Воронка найма»).

Ни боевых учёток, ни сети: только 127.0.0.1, своя схема со случайным именем, в конце она
удаляется. Номера выдуманы.

    SYNTONY_LEADS_TEST_PORT=55471 python -m pytest tests/test_syntony_leads_postgres.py
"""

import contextlib
import os
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PORT = os.environ.get('SYNTONY_LEADS_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (SYNTONY_LEADS_TEST_PORT)')

if not hasattr(time, 'tzset'):          # db/core.py зовёт tzset при импорте; на Windows его нет
    time.tzset = lambda: None

UTC = timezone.utc


@pytest.fixture()
def pg():
    import psycopg2
    schema = 'syntony_leads_%s' % uuid.uuid4().hex[:10]
    conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres')
    conn.autocommit = True
    with conn.cursor() as cursor:
        cursor.execute('CREATE SCHEMA %s' % schema)
        cursor.execute('SET search_path TO %s' % schema)
    try:
        yield conn
    finally:
        with conn.cursor() as cursor:
            cursor.execute('DROP SCHEMA %s CASCADE' % schema)
        conn.close()


@pytest.fixture()
def db(pg):
    from db.amo_leads import AmoLeadsMixin
    from db.schema import SchemaMixin

    class Db(AmoLeadsMixin, SchemaMixin):
        @contextlib.contextmanager
        def _get_cursor(self):
            with pg.cursor() as cursor:
                yield cursor

    instance = Db.__new__(Db)
    with pg.cursor() as cursor:
        instance._init_amo_leads_schema_tx(cursor)
    return instance


def lead(lead_id, created, **fields):
    row = {'lead_id': lead_id, 'created_at': created, 'updated_at': created, 'park': 'iTaxi',
           'pipeline_kind': 'sales', 'source': 'facebook', 'created_by_channel': 'lead_ads',
           'created_by': 'client', 'tags': '', 'utm_source': 'facebook', 'utm_medium': 'lead_ads',
           'utm_campaign': 'iTaxi/16.09.2026', 'form_name': None, 'stage_type': 'new'}
    row.update(fields)
    return row


def test_schema_is_idempotent(db, pg):
    with pg.cursor() as cursor:
        db._init_amo_leads_schema_tx(cursor)
        cursor.execute("SELECT to_regclass('syntony_leads') IS NOT NULL, "
                       "to_regclass('syntony_lead_syncs') IS NOT NULL")
        assert cursor.fetchone() == (True, True)


def test_upsert_writes_new_and_changed_rows_and_leaves_the_rest(db, pg):
    t = datetime(2026, 11, 19, 5, 0, tzinfo=UTC)
    assert db.upsert_syntony_leads([lead('a', t), lead('b', t, tags='call_jana_wb1', source='phone')]) == 2
    with pg.cursor() as cursor:
        cursor.execute("SELECT lead_id, xmin::text FROM syntony_leads ORDER BY lead_id")
        before = dict(cursor.fetchall())

    db.upsert_syntony_leads([lead('a', t), lead('b', t, tags='call_jana_wb1, call_jana_olx', source='phone')])
    with pg.cursor() as cursor:
        cursor.execute("SELECT lead_id, xmin::text, tags FROM syntony_leads ORDER BY lead_id")
        after = {row[0]: row[1:] for row in cursor.fetchall()}
    assert after['a'][0] == before['a'], 'неизменившуюся заявку не переписываем'
    assert after['b'][0] != before['b']
    assert after['b'][1] == 'call_jana_wb1, call_jana_olx'
    assert db.upsert_syntony_leads([]) == 0


def test_leads_between_is_half_open_and_carries_what_counting_needs(db):
    start = datetime(2026, 11, 19, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    db.upsert_syntony_leads([
        lead('before', start - timedelta(seconds=1)),
        lead('first', start, tags='fb_forma_itaxi_obshchaya'),
        lead('last', start + timedelta(hours=12) - timedelta(seconds=1)),
        lead('after', start + timedelta(hours=12)),
    ])
    rows = db.get_syntony_leads_between(start, start + timedelta(hours=12))
    assert sorted(r['created_at'] for r in rows) == [start, start + timedelta(hours=12) - timedelta(seconds=1)]
    first = next(r for r in rows if r['created_at'] == start)
    assert first == {'created_at': start, 'tags': 'fb_forma_itaxi_obshchaya', 'utm_source': 'facebook',
                     'utm_campaign': 'iTaxi/16.09.2026', 'source': 'facebook', 'form_name': None}


def test_leads_between_leave_out_our_missed_call_requests(db):
    # Заявку на перезвон заводит наш робот пропущенных (SALES-4) — это не лид: отбивка её не
    # считает, как не считала сделки amoCRM с тегом «Пропущенный входящий». Тег формы
    # выбирают в Syntony — здесь он нарочно «звонковый», чтобы без исключения попасть в «Звонки».
    start = datetime(2026, 11, 23, 0, 0, tzinfo=timezone(timedelta(hours=5)))
    db.upsert_syntony_leads([
        lead('ours', start, source='website', created_by_channel='web_form',
             tags='call_цр_missed', utm_source='icore', utm_medium='missed_call', utm_campaign=None),
        lead('other_medium', start, utm_source='icore', utm_medium='site'),
        lead('no_marks', start, source='phone', tags='call_jana_wb1', utm_source=None,
             utm_medium=None, utm_campaign=None),
        lead('fb', start),
    ])
    rows = db.get_syntony_leads_between(start, start + timedelta(hours=1))
    assert sorted((r['utm_source'] or '', r['tags']) for r in rows) == [
        ('', 'call_jana_wb1'), ('facebook', ''), ('icore', '')]


def test_amo_leads_between_gives_time_and_campaign_for_twins(db, pg):
    t = datetime(2026, 11, 12, 4, 0, tzinfo=UTC)
    with pg.cursor() as cursor:
        cursor.execute("""
            INSERT INTO amo_leads (lead_id, name, created_at, created_date, tags, utm_source, utm_campaign)
            VALUES (1, 'x', %s, %s, 'forma_itaxi_общая', 'fb', 'iTaxi/16.09.2026')
        """, (t, t.date()))
    rows = db.get_amo_leads_between(t, t + timedelta(seconds=1))
    assert rows == [{'created_at': t, 'tags': 'forma_itaxi_общая', 'utm_source': 'fb',
                     'utm_campaign': 'iTaxi/16.09.2026'}]


def test_sync_journal_filters(db):
    full = db.start_syntony_lead_sync('full')
    db.finish_syntony_lead_sync(full, leads_fetched=10)
    failed = db.start_syntony_lead_sync('updated')
    db.finish_syntony_lead_sync(failed, error='Syntony: HTTP 429')
    running = db.start_syntony_lead_sync('updated')

    assert db.get_last_syntony_lead_sync()['id'] == failed
    assert db.get_last_syntony_lead_sync(status='ok')['id'] == full
    assert db.get_last_syntony_lead_sync(status='ok', mode='full')['id'] == full
    assert db.get_last_syntony_lead_sync(status='ok', mode='updated') is None
    assert db.get_last_syntony_lead_sync(finished=False)['id'] == running
    assert db.get_last_syntony_lead_sync()['error'] == 'Syntony: HTTP 429'


# ── журнал робота OLX: исход `received` ───────────────────────────────────────

@pytest.fixture()
def olx(pg):
    from olx_amo import schema as olx_schema
    with pg.cursor() as cursor:
        olx_schema.init_olx_amo_schema(cursor)
    return pg


def _write(cursor, result, phone, created, cabinet):
    """Строка журнала робота с заданным временем разбора (write_journal ставит «сейчас»)."""
    cursor.execute("""
        INSERT INTO olx_journal (cabinet_code, thread_id, message_id, message_at, phone_raw,
                                 phone_normalized, tag, result, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (cabinet, 't-%s' % phone, 'm-%s-%s' % (phone, result), created, phone, phone,
          'forma_olx_%s' % cabinet, result, created))


def test_received_rows_feed_the_report_and_only_them(olx):
    from olx_amo import queries
    noon = datetime(2026, 11, 19, 12, 0)
    with olx.cursor() as cursor:
        _write(cursor, 'received', '77010000001', noon - timedelta(hours=1), 'itaxi')
        _write(cursor, 'received', '77010000002', noon - timedelta(minutes=1), 'arenda')
        _write(cursor, 'duplicate', '77010000001', noon - timedelta(minutes=50), 'itaxi')
        _write(cursor, 'lead_created', '77010000003', noon - timedelta(hours=2), 'jana')
        _write(cursor, 'manual_review', '77010000005', noon - timedelta(hours=3), 'jana')
        _write(cursor, 'received', '77010000004', noon, 'jana')
        rows = queries.received_between(cursor, datetime(2026, 11, 19, 0, 0), noon)
    assert sorted(rows) == [('arenda', noon - timedelta(minutes=1)), ('itaxi', noon - timedelta(hours=1))]


def test_received_number_makes_the_same_number_a_duplicate_that_day(olx):
    from olx_amo import queries
    day = datetime(2026, 11, 19, 9, 0)
    with olx.cursor() as cursor:
        _write(cursor, 'received', '77010000001', day, 'itaxi')
        assert queries.find_recent_lead(cursor, 'itaxi', '77010000001', day=day.date())
        assert not queries.find_recent_lead(cursor, 'jana', '77010000001', day=day.date())
        assert not queries.find_recent_lead(cursor, 'itaxi', '77010000001', day=date(2026, 11, 20))


def test_daily_summary_counts_numbers_with_or_without_a_deal(olx):
    from olx_amo import queries
    day = datetime(2026, 11, 19, 9, 0)
    with olx.cursor() as cursor:
        _write(cursor, 'received', '77010000001', day, 'itaxi')
        _write(cursor, 'lead_created', '77010000002', day, 'itaxi')
        _write(cursor, 'needs_human', None, day, 'itaxi')
        _write(cursor, 'duplicate', '77010000001', day, 'itaxi')
        summary = queries.daily_summary(cursor, day=day.date())
    row = next(r for r in summary if r['cabinet_code'] == 'itaxi')
    assert (row['total'], row['leads'], row['duplicates']) == (4, 2, 1)


def test_waiting_mark_is_set_once_on_the_real_table(olx):
    from olx_amo import queries
    with olx.cursor() as cursor:
        assert not queries.mark_awaiting_human(cursor, 'itaxi', 'нет-такого'), 'строки чата нет — метки нет'
        queries.upsert_thread(cursor, 'itaxi', 't-1')
        assert queries.mark_awaiting_human(cursor, 'itaxi', 't-1')
        assert not queries.mark_awaiting_human(cursor, 'itaxi', 't-1')


def test_first_message_of_a_new_chat_waits_for_a_human_without_the_canned_reply(olx, monkeypatch):
    """Автоответ выключен: первое сообщение без номера в совсем новом чате обязано попасть и
    в журнал, и в «ждут ответа». Раньше строку чата заводил автоответ, а метка ставится
    UPDATE'ом этой строки (опыт разбора 19.11 на настоящей базе)."""
    from olx_amo import cabinets, queries, service
    monkeypatch.delenv('OLX_CANNED_REPLY_ENABLED', raising=False)
    monkeypatch.delenv('OLX_AMO_LEADS_ENABLED', raising=False)

    class Db(object):
        """Как Database._get_cursor: курсор внутри транзакции (журнал ставит SAVEPOINT)."""
        @contextlib.contextmanager
        def _get_cursor(self):
            with olx.cursor() as cursor:
                cursor.execute('BEGIN')
                try:
                    yield cursor
                except Exception:
                    cursor.execute('ROLLBACK')
                    raise
                cursor.execute('COMMIT')

    class Olx(object):
        sent = []

        def send_message(self, thread_id, text):
            self.sent.append(text)

    cabinet = cabinets.BY_CODE['itaxi']
    counters = service.CabinetResult(cabinet.code)
    message = {'id': 'm1', 'type': 'received', 'text': 'Здравствуйте, какие условия?',
               'created_at': datetime.now(timezone.utc).isoformat()}
    outcome = service._handle_message(Db(), service._Writers(Olx(), None), cabinet,
                                      {'id': 'новый-чат'}, message, counters)
    assert outcome == 'needs_human'
    assert Olx.sent == []
    with olx.cursor() as cursor:
        cursor.execute("SELECT result FROM olx_journal WHERE thread_id = 'новый-чат'")
        assert [r[0] for r in cursor.fetchall()] == ['needs_human']
        state = queries.get_thread(cursor, 'itaxi', 'новый-чат')
    assert state and state['awaiting_human_since'] is not None


def test_hiring_takes_received_numbers_of_its_cabinets(olx):
    from hiring import queries as hiring_queries
    day = datetime(2026, 11, 19, 9, 0)
    with olx.cursor() as cursor:
        _write(cursor, 'received', '77010000001', day, 'itaxi')
        _write(cursor, 'needs_human', None, day, 'itaxi')
        rows = hiring_queries.olx_responses_since(cursor, 0, ['itaxi'])
    assert [r['phone_normalized'] for r in rows] == ['77010000001']
