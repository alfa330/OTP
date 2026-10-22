# -*- coding: utf-8 -*-
"""Раздел «АТС» на настоящем SQL: PBX_TEST_PORT — порт локального Postgres (пользователь postgres,
вход без пароля). Своя база со случайным именем, в конце удаляется; номера выдуманы.

    PBX_TEST_PORT=55446 python -m pytest tests/test_pbx_postgres.py

Что закреплено:
  * строка звонка переписывается и получает новую ревизию, только если что-то изменилось:
    повтор той же пачки (полный снимок раз в минуту) ничего не трогает;
  * законченный звонок не оживает от старой пачки, пришедшей после обрыва;
  * снимок закрывает звонки, которых в нём нет, — с новой ревизией, чтобы зрители узнали;
  * лента «новее моей ревизии» — по порядку; отметка коннектора возвращает прежнее
    «станция видна», а пульс канала команд это состояние не затирает;
  * слушатель реалтайма: NOTIFY после коммита → звонки в памяти процесса и событие потокам;
    команда будит канал коннектора; «board» и «conn» доходят как есть.
"""
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PORT = os.environ.get('PBX_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (PBX_TEST_PORT)')

if PORT:
    import psycopg2

    from pbx import queries, realtime, schema


def _connect(dbname):
    return psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname=dbname)


@pytest.fixture(scope='module')
def dbname():
    name = 'pbx_test_%s' % uuid.uuid4().hex[:10]
    admin = _connect('postgres')
    admin.autocommit = True
    admin.cursor().execute('CREATE DATABASE %s' % name)
    conn = _connect(name)
    with conn, conn.cursor() as cursor:
        cursor.execute("""
            CREATE TABLE users (id integer PRIMARY KEY, name text, sip_number text);
            CREATE TABLE operator_profiles (user_id integer PRIMARY KEY, sip_number text);
            INSERT INTO users VALUES (1, 'Оператор', '6726'), (2, 'Без номера', '');
            INSERT INTO operator_profiles VALUES (2, '6727');
        """)
        schema.init_pbx_schema(cursor)
        schema.init_pbx_schema(cursor)          # идемпотентно
        assert schema.schema_is_ready(cursor)
    conn.close()
    yield name
    admin.cursor().execute('DROP DATABASE %s WITH (FORCE)' % name)
    admin.close()


@pytest.fixture
def conn(dbname):
    connection = _connect(dbname)
    with connection, connection.cursor() as cursor:
        cursor.execute('TRUNCATE pbx_calls, pbx_commands, pbx_connector_state')
    yield connection
    connection.close()


def call(**over):
    base = {
        'linkedid': '1.100', 'direction': 'in', 'phone': '7015550001', 'did': '7475550033', 'queue': '3034',
        'ext': '', 'peer_ext': '', 'ringing': [], 'state': 'queued', 'started_at': '2026-10-22T12:00:00',
        'queued_at': '2026-10-22T12:00:05', 'ringing_at': '', 'answered_at': '', 'ended_at': '',
        'wait_seconds': None, 'hangup_cause': None, 'hangup_side': '', 'dial_prefix': '', 'dial_status': '',
        'channel': 'PJSIP/+77475550033_jana-1', 'agent_channel': '', 'recording_file': '', 'origin': '',
        'command_id': None, 'seq': 1,
    }
    base.update(over)
    return queries.clean_call(base)


def test_rev_moves_only_when_the_row_changes(conn):
    with conn, conn.cursor() as cursor:
        assert queries.upsert_calls(cursor, [call()]) == ['1.100']
        first = queries.call_by_linkedid(cursor, '1.100')['rev']
        # Тот же звонок полным снимком (другой seq коннектора) — ничего не изменилось.
        assert queries.upsert_calls(cursor, [call(seq=7)]) == []
        assert queries.call_by_linkedid(cursor, '1.100')['rev'] == first
        assert queries.upsert_calls(cursor, [call(state='talking', ext='6726', answered_at='2026-10-22T12:00:09',
                                                  agent_channel='PJSIP/6726-2', ringing=['6726'])]) == ['1.100']
        row = queries.call_by_linkedid(cursor, '1.100')
        assert row['rev'] > first
        assert (row['state'], row['ext'], row['ringing']) == ('talking', '6726', ['6726'])
        assert row['answered_at'] == '2026-10-22T12:00:09'


def test_ended_call_does_not_come_back_to_life(conn):
    with conn, conn.cursor() as cursor:
        queries.upsert_calls(cursor, [call(state='ended', ended_at='2026-10-22T12:01:00', hangup_cause=16)])
        rev = queries.call_by_linkedid(cursor, '1.100')['rev']
        assert queries.upsert_calls(cursor, [call(state='talking', ext='6726')]) == []
        row = queries.call_by_linkedid(cursor, '1.100')
        assert (row['state'], row['rev']) == ('ended', rev)
        # Уточнение законченного (причина отбоя дошла позже) — принимается.
        assert queries.upsert_calls(cursor, [call(state='ended', ended_at='2026-10-22T12:01:00',
                                                  hangup_cause=17)]) == ['1.100']


def test_snapshot_closes_missing_calls_with_a_new_rev(conn):
    now = datetime(2026, 10, 22, 12, 5, 0)
    with conn, conn.cursor() as cursor:
        queries.upsert_calls(cursor, [call(), call(linkedid='1.200', phone='7015550002')])
        before = queries.max_rev(cursor)
        assert queries.close_missing(cursor, ['1.100'], now=now) == 1
        gone = queries.call_by_linkedid(cursor, '1.200')
        assert gone['state'] == 'ended' and gone['ended_at'] == '2026-10-22T12:05:00'
        assert gone['rev'] > before
        assert [c['linkedid'] for c in queries.calls_since(cursor, before)] == ['1.200']
        assert [c['linkedid'] for c in queries.active_calls(cursor, now=now)] == ['1.100']


def test_calls_since_is_ordered_by_rev(conn):
    with conn, conn.cursor() as cursor:
        queries.upsert_calls(cursor, [call(linkedid='a.1'), call(linkedid='b.1')])
        queries.upsert_calls(cursor, [call(linkedid='a.1', state='ringing', ringing=['6726'])])
        revs = [(c['linkedid'], c['rev']) for c in queries.calls_since(cursor, 0)]
        assert [lid for lid, _rev in revs] == ['b.1', 'a.1']
        assert revs[0][1] < revs[1][1]


def test_connector_seen_reports_the_previous_station_state(conn):
    with conn, conn.cursor() as cursor:
        assert queries.connector_seen(cursor, version='pbx/2.0.0', ami={'connected': True, 'host': 'x'}) is None
        assert queries.connector_seen(cursor, ami={'connected': False, 'error': 'down'}) is True
        # Пульс канала команд не знает о станции — и не затирает то, что прислала пачка.
        assert queries.connector_seen(cursor, stream=True) is False
        state = queries.connector_state(cursor)
        assert state['ami_connected'] is False and state['ami_error'] == 'down'
        assert state['stream_at'] is not None
        assert state['version'] == 'pbx/2.0.0'


def test_commands_claim_and_finish(conn):
    with conn, conn.cursor() as cursor:
        first = queries.enqueue_command(cursor, 'originate', {'ext': '6726', 'dial': '3333*77015550001'}, 1)
        second = queries.enqueue_command(cursor, 'hangup', {'channel': 'PJSIP/6726-1'}, 1)
        claimed = queries.claim_commands(cursor)
        assert [c['id'] for c in claimed] == [first, second]
        assert claimed[0]['dial'] == '3333*77015550001'
        assert queries.claim_commands(cursor) == []
        assert queries.finish_command(cursor, first, 'done', 'телефон ответил', '9.9')
        assert not queries.finish_command(cursor, first, 'accepted'), 'конечный итог не откатывается'
        assert queries.command(cursor, first)['status'] == 'done'
        assert queries.user_ext(cursor, 1) == '6726'
        assert queries.user_ext(cursor, 2) == '6727'
        # Ушла в оборванный канал: подтверждения нет дольше SENT_ACK_SECONDS — протухла, повтора нет.
        cursor.execute("UPDATE pbx_commands SET sent_at = NOW() - interval '31 seconds' WHERE id = %s", (second,))
        assert queries.claim_commands(cursor) == []
        lost = queries.command(cursor, second)
        assert lost['status'] == 'expired' and 'связь оборвалась' in lost['message']


def test_listener_delivers_changes_after_commit(dbname, conn):
    hub = realtime.Hub(local_now=lambda: datetime(2026, 10, 22, 12, 0, 30))
    thread = threading.Thread(target=realtime._run_listener, args=(lambda: _connect(dbname), hub), daemon=True)
    thread.start()
    assert realtime.wait_ready(hub, timeout=10), 'слушатель не прочитал базу'
    cursor_seq = hub.current_seq()

    with conn, conn.cursor() as cursor:
        queries.upsert_calls(cursor, [call(state='talking', ext='6726', answered_at='2026-10-22T12:00:09')])
        realtime.notify(cursor, 'calls')
        # До коммита слушатель ничего не видит: NOTIFY уходит вместе с транзакцией.
        events, _ = hub.wait(cursor_seq, timeout=0.3)
        assert events == []
    events, cursor_seq = hub.wait(cursor_seq, timeout=5)
    calls = [c for e in events if e['kind'] == 'calls' for c in e['calls']]
    assert [c['linkedid'] for c in calls] == ['1.100']
    assert hub.active_calls()[0]['ext'] == '6726'

    cmd_before = hub.current_cmd_seq()
    with conn, conn.cursor() as cursor:
        realtime.notify(cursor, 'cmd')
        realtime.notify(cursor, 'board', why='status', op=42)
        realtime.notify(cursor, 'conn', seen=time.time(), ami=True, err=None, ver='pbx/2.0.0')
    deadline = time.monotonic() + 5
    board = []
    while time.monotonic() < deadline and not (board and hub.current_cmd_seq() > cmd_before):
        events, cursor_seq = hub.wait(cursor_seq, timeout=0.5)
        board += [e for e in events if e['kind'] == 'board']
    assert hub.current_cmd_seq() > cmd_before, 'команда будит канал коннектора'
    assert board == [{'kind': 'board', 'why': 'status', 'operator_id': 42}]
    view = hub.connector_view()
    assert view['online'] is True and view['version'] == 'pbx/2.0.0'

    # Две записи коммитятся не по порядку ревизий: меньшая становится видна позже большей.
    slow = _connect(dbname)
    slow_cursor = slow.cursor()
    queries.upsert_calls(slow_cursor, [call(linkedid='2.1', phone='7015550002')])     # меньшая ревизия, без коммита
    with conn, conn.cursor() as cursor:
        queries.upsert_calls(cursor, [call(linkedid='2.2', phone='7015550003')])      # большая — коммит первой
        realtime.notify(cursor, 'calls')
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and '2.2' not in {c['linkedid'] for c in hub.active_calls()}:
        hub.wait(hub.current_seq(), timeout=0.2)
    realtime.notify(slow_cursor, 'calls')
    slow.commit()
    slow.close()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and '2.1' not in {c['linkedid'] for c in hub.active_calls()}:
        hub.wait(hub.current_seq(), timeout=0.2)
    assert {'2.1', '2.2'} <= {c['linkedid'] for c in hub.active_calls()}, 'запоздавший коммит не потерян'


def test_partial_call_from_the_channel_list_does_not_erase_what_is_known(conn):
    """Коннектор перезапустился посреди разговора: звонок восстановлен из списка каналов, где нет
    ни входа в очередь, ни момента ответа, ни DID. Запись такого звонка не затирает известное."""
    with conn, conn.cursor() as cursor:
        queries.upsert_calls(cursor, [call(state='talking', ext='6726', answered_at='2026-10-22T12:00:09',
                                           queued_at='2026-10-22T12:00:05', wait_seconds=4)])
        rev = queries.call_by_linkedid(cursor, '1.100')['rev']
        restored = call(state='talking', ext='6726', did='', queue='3034', queued_at='',
                        answered_at='2026-10-22T12:05:00', started_at='2026-10-22T12:00:01', partial=True)
        assert queries.upsert_calls(cursor, [restored]) == [], 'нового в восстановленном нет'
        row = queries.call_by_linkedid(cursor, '1.100')
        assert row['answered_at'] == '2026-10-22T12:00:09', 'момент ответа — прежний, точный'
        assert row['queued_at'] == '2026-10-22T12:00:05'
        assert row['did'] == '7475550033', 'пустое из списка не затирает DID'
        assert row['wait_seconds'] == 4
        assert row['started_at'] == '2026-10-22T12:00:00', 'начало — известное'
        assert row['rev'] == rev
        # Новое в восстановленном (оператор сменился переводом) — принимается.
        assert queries.upsert_calls(cursor, [dict(restored, ext='6727')]) == ['1.100']
        assert queries.call_by_linkedid(cursor, '1.100')['ext'] == '6727'
        # А конец звонка частичный звонок передаёт как есть.
        assert queries.upsert_calls(cursor, [dict(restored, state='ended', ended_at='2026-10-22T12:06:00',
                                                  hangup_side='client')]) == ['1.100']
        row = queries.call_by_linkedid(cursor, '1.100')
        assert (row['state'], row['ended_at'], row['hangup_side']) == ('ended', '2026-10-22T12:06:00', 'client')
