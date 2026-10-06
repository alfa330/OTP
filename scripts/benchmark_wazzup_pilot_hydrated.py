"""Six local HTTP SSE clients with real PostgreSQL hydration and query counts.

Uses only a disposable localhost PostgreSQL instance, synthetic message data,
and a temporary random schema. Does not load env files or contact Wazzup.
Run: python scripts/benchmark_wazzup_pilot_hydrated.py --port 15436
"""
import argparse
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import benchmark_wazzup_pilot as transport_benchmark  # noqa: E402
from wazzup.realtime import broadcast_changes  # noqa: E402


def run(args):
    import psycopg2

    schema = 't_pilot_hydration_bench_' + uuid.uuid4().hex
    connection = psycopg2.connect(host='127.0.0.1', port=args.port, dbname='postgres',
                                  user='postgres', connect_timeout=3)
    connection.autocommit = True
    cursor = connection.cursor()
    original_broker = transport_benchmark.LocalBroker
    counter = {'sql_queries': 0, 'content_changes': 0, 'status_only_changes': 0}

    class CountedCursor:
        def execute(self, *arguments):
            counter['sql_queries'] += 1
            cursor.execute(*arguments)

        def fetchall(self):
            return cursor.fetchall()

    class HydratedBroker(original_broker):
        def publish(self, event):
            if 'publishedNs' not in event:
                return super().publish(event)
            number = int(event['messageId'].rsplit('-', 1)[1])
            status_only = number % 3 != 0
            counter['status_only_changes' if status_only else 'content_changes'] += 1
            event = dict(event, statusOnly=status_only, affectsList=not status_only)
            # Fan-out path is unchanged; intercept only the benchmark producer
            # to include the production hydration code and its real SELECT.
            sink = SimpleNamespace(condition=self.condition, streams=self.streams,
                                   publish=super().publish)
            broadcast_changes(CountedCursor(), [event], sink)

    try:
        cursor.execute('CREATE SCHEMA ' + schema)
        cursor.execute('SET search_path TO ' + schema)
        cursor.execute("SET statement_timeout TO '5s'")
        cursor.execute('''
            CREATE TABLE wazzup_messages (
                message_id TEXT PRIMARY KEY, account TEXT, channel_id TEXT, chat_id TEXT,
                dt TIMESTAMPTZ, is_echo BOOLEAN, type TEXT, text TEXT, content_uri TEXT,
                author_name TEXT, author_id TEXT, status TEXT, is_edited BOOLEAN,
                is_deleted BOOLEAN, wazzup_dt TIMESTAMPTZ
            );
            CREATE TABLE wazzup_pilot_outbox (account TEXT, message_id TEXT, author_name TEXT);
            CREATE TABLE wazzup_chats (
                account TEXT, channel_id TEXT, chat_id TEXT, chat_type TEXT,
                contact_name TEXT, contact_phone TEXT, last_message_at TIMESTAMPTZ,
                last_message_text TEXT, last_message_is_echo BOOLEAN, messages_count INTEGER,
                inbound_count INTEGER, outbound_count INTEGER, PRIMARY KEY(channel_id,chat_id)
            );
            INSERT INTO wazzup_chats VALUES ('op','synthetic-channel','synthetic-chat','whatsapp',
                'Synthetic contact','70000000000',now(),'Synthetic body',true,300,150,150);
        ''')
        cursor.execute('''INSERT INTO wazzup_messages
            SELECT 'local-'||i::text,'op','synthetic-channel','synthetic-chat',now(),true,'text',
                   repeat('Synthetic body ',40),NULL,'Synthetic operator',NULL,'read',false,false,NULL
              FROM generate_series(0,%s) i''', (args.events - 1,))
        transport_benchmark.LocalBroker = HydratedBroker
        result = transport_benchmark.run(args)
        result['scope'] = 'LOCAL PostgreSQL hydration -> shared broker -> Waitress -> 6 HTTP SSE readers'
        result['excluded'] = ['Wazzup', 'webhook processing', 'PostgreSQL NOTIFY wait',
                              'reverse proxy', 'real authentication', 'browser rendering']
        result['hydration'] = dict(counter,
            queries_per_content_change=counter['sql_queries'] / max(1, counter['content_changes']),
            query_count_independent_of_clients=True)
        result['passed'] = result['passed'] and counter['sql_queries'] == counter['content_changes']
        return result
    finally:
        transport_benchmark.LocalBroker = original_broker
        cursor.execute('DROP SCHEMA IF EXISTS ' + schema + ' CASCADE')
        cursor.close()
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True, help='Disposable localhost PostgreSQL port')
    parser.add_argument('--clients', type=int, default=6)
    parser.add_argument('--events', type=int, default=300)
    parser.add_argument('--rate', type=float, default=30)
    parser.add_argument('--threads', type=int, default=96)
    parser.add_argument('--background-streams', type=int, default=24)
    result = run(parser.parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
