"""Small, strictly read-only chat query sample; never imports the application.

Six clients share at most two connections because the audit role permits two.
Only aggregate timings and row counts are printed; message content stays in RAM.
Run explicitly: python scripts/benchmark_wazzup_pilot_db.py --run
"""
import argparse
import json
import math
import os
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]


def stats(values):
    values = sorted(values)
    def percentile(fraction):
        return round(values[max(0, math.ceil(len(values) * fraction) - 1)], 3)
    return {'samples': len(values), 'p50_ms': percentile(.5), 'p95_ms': percentile(.95),
            'max_ms': round(values[-1], 3)}


def readonly_dsn():
    dsn = os.getenv('DATABASE_URL_READONLY')
    if not dsn:
        match = re.search(r'^DATABASE_URL_READONLY\s*=\s*(.+)$',
                          (ROOT / '.env.codex.local').read_text(encoding='utf-8-sig'), re.M)
        dsn = match.group(1).strip().strip('\"\'') if match else None
    if not dsn:
        raise RuntimeError('No read-only database configuration')
    return dsn


def run():
    dsn = readonly_dsn()
    connections = []
    try:
        # Connection policy is explicit before the first statement on every connection.
        def connect():
            conn = psycopg2.connect(dsn, connect_timeout=10)
            conn.set_session(readonly=True, autocommit=False)
            connections.append(conn)
            return conn

        first = connect()
        with first.cursor() as cur:
            cur.execute('SET LOCAL statement_timeout = 5000')
            cur.execute('SHOW transaction_read_only')
            if cur.fetchone()[0] != 'on':
                raise RuntimeError('Read-only session was not established')
            cur.execute('SELECT rolconnlimit FROM pg_roles WHERE rolname=current_user')
            role_limit = cur.fetchone()[0]
            cur.execute("SELECT channel_id,chat_id FROM wazzup_chats WHERE account='op' "
                        'ORDER BY last_message_at DESC NULLS LAST LIMIT 1')
            chat = cur.fetchone()
            if not chat:
                raise RuntimeError('No verifier chat available for sampling')
        first.rollback()
        pool_size = min(2, role_limit) if role_limit > 0 else 2
        for _ in range(pool_size - 1):
            connect()
        pool = queue.Queue()
        for conn in connections:
            pool.put(conn)

        queries = {
            'chat_count': ('SELECT COUNT(*) FROM wazzup_chats WHERE account=%s', ['op']),
            'chat_list_30': (
                'SELECT channel_id,chat_id,chat_type,contact_name,contact_phone,last_message_at,'
                'last_message_text,last_message_is_echo,messages_count,inbound_count,outbound_count '
                'FROM wazzup_chats WHERE account=%s ORDER BY last_message_at DESC NULLS LAST LIMIT 30', ['op']),
        }
        for limit in (50, 200):
            queries[f'messages_{limit}'] = (
                'SELECT message_id,dt,is_echo,type,text,content_uri,author_name,author_id,status,'
                'is_edited,is_deleted,wazzup_dt FROM wazzup_messages '
                'WHERE account=%s AND channel_id=%s AND chat_id=%s '
                'ORDER BY dt DESC,message_id DESC LIMIT %s', ['op', *chat, limit])
        timings = {key: [] for key in queries}
        rows = {key: [] for key in queries}
        queue_wait, cycle_times = [], []
        barrier = threading.Barrier(6)

        def client(_):
            barrier.wait(timeout=10)
            for _ in range(3):
                cycle_start = time.perf_counter()
                conn = pool.get(timeout=30)
                queue_wait.append((time.perf_counter() - cycle_start) * 1000)
                try:
                    with conn.cursor() as cur:
                        cur.execute('SET LOCAL statement_timeout = 5000')
                        for name, (sql, params) in queries.items():
                            before = time.perf_counter()
                            cur.execute(sql, params)
                            data = cur.fetchall()
                            timings[name].append((time.perf_counter() - before) * 1000)
                            rows[name].append(len(data))
                    conn.rollback()
                finally:
                    pool.put(conn)
                cycle_times.append((time.perf_counter() - cycle_start) * 1000)

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(client, range(6)))
        return {
            'scope': 'Actual read-only DB SELECT latency from this workstation, includes network RTT',
            'limitations': 'Two audit-role connections; one recent chat; excludes API auth, refresh join, UI',
            'clients': 6, 'iterations_per_client': 3, 'readonly_connections': pool_size,
            'role_connection_limit': role_limit, 'data_selects': sum(map(len, timings.values())),
            'elapsed_seconds': round(time.perf_counter() - start, 3),
            'queries': {name: dict(stats(values), min_rows=min(rows[name]), max_rows=max(rows[name]))
                        for name, values in timings.items()},
            'connection_queue': stats(queue_wait), 'four_query_cycle_including_queue': stats(cycle_times),
        }
    finally:
        for conn in connections:
            conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Run the bounded read-only sample')
    args = parser.parse_args()
    if not args.run:
        parser.error('Pass --run to execute this bounded read-only sample')
    try:
        print(json.dumps(run(), indent=2))
    except Exception as error:
        # Do not expose DSN, chat identifiers, or vendor/database error text.
        print(json.dumps({'error_type': type(error).__name__, 'completed': False}))
        raise SystemExit(1)
