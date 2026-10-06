"""Local Waitress + actual pilot SSE transport benchmark (no external services).

Run: python scripts/benchmark_wazzup_pilot.py --clients 6 --events 300 --rate 30
Optional: --background-streams 24 models other long-lived portal connections.

Latency starts at EventBroker.publish and ends in a local HTTP SSE reader. It
does NOT measure Wazzup/webhook/PG/proxy latency, DB refresh, or browser rendering.
Auth uses a fake alfa330 record and every event is synthetic. No env files read.
"""
import argparse
import importlib.metadata
import json
import math
import platform
import sys
import threading
import time
from pathlib import Path

import requests
from flask import Flask, Response, jsonify
from waitress import create_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wazzup.pilot import STREAM_LIMIT, build_pilot_blueprint  # noqa: E402
from wazzup.realtime import EventBroker  # noqa: E402


class FakeDatabase:
    def get_user(self, **_):
        actor = [None] * 20
        actor[0], actor[2], actor[7], actor[10] = 42, 'Synthetic operator', 'alfa330', True
        actor[11] = 'working'
        return actor


class LocalBroker(EventBroker):
    def wait(self, after, timeout=0.25):
        # Short heartbeat only speeds harness shutdown; production data path is
        # inherited unchanged, and production default heartbeat is 20 seconds.
        return super().wait(after, timeout=timeout)


def summary(values):
    values = sorted(values)
    if not values:
        return {'count': 0}
    quantile = lambda fraction: round(values[max(0, math.ceil(len(values) * fraction) - 1)], 3)
    return {'count': len(values), 'p50_ms': quantile(.5), 'p95_ms': quantile(.95),
            'p99_ms': quantile(.99), 'max_ms': round(values[-1], 3)}


def run(args):
    if not 1 <= args.clients <= STREAM_LIMIT:
        raise ValueError(f'clients must be between 1 and pilot cap {STREAM_LIMIT}')
    if args.threads < args.clients + args.background_streams + 4:
        raise ValueError('Keep at least four free Waitress threads for ordinary requests')
    if args.events < 1 or args.rate <= 0 or args.background_streams < 0:
        raise ValueError('events/rate must be positive and background-streams nonnegative')

    broker, stop = LocalBroker(), threading.Event()
    broker.set_ready(True)
    app = Flask(__name__)
    app.register_blueprint(build_pilot_blueprint(
        db=FakeDatabase(), require_api_key=lambda fn: fn, guard=lambda: (42, None),
        channels=lambda account: [], preflight=lambda: ('', 204),
        listen_connect=lambda: None, event_broker=broker,
    ))

    @app.get('/_benchmark/probe')
    def probe():
        return jsonify(ok=True)

    @app.get('/_benchmark/background-stream')
    def background_stream():
        def generate():
            yield ': connected\n\n'
            while not stop.wait(.25):
                yield ': heartbeat\n\n'
        return Response(generate(), mimetype='text/event-stream')

    server = create_server(app, host='127.0.0.1', port=0, threads=args.threads,
                           connection_limit=max(180, args.clients + args.background_streams + 20))
    server_thread = threading.Thread(target=server.run, daemon=True, name='benchmark-waitress')
    server_thread.start()
    origin = f'http://127.0.0.1:{server.effective_port}'
    errors, responses, latencies, probe_latencies = [], [], [], []
    seen = [set() for _ in range(args.clients)]
    ready = [threading.Event() for _ in range(args.clients + args.background_streams)]

    def reader(index, background=False):
        session = requests.Session()
        session.trust_env = False
        path = '/_benchmark/background-stream' if background else '/api/wazzup/pilot/stream?account=op'
        try:
            with session.get(origin + path, stream=True, timeout=(5, 5)) as response:
                response.raise_for_status()
                responses.append(response)
                for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                    if line.startswith('event: connected') or line.startswith(': connected'):
                        ready[index].set()
                    if not background and line.startswith('data: '):
                        payload = json.loads(line[6:])
                        received = time.perf_counter_ns()
                        for event in payload.get('changes', []):
                            event_id = event['messageId']
                            if event_id not in seen[index]:
                                seen[index].add(event_id)
                                latencies.append((received - event['publishedNs']) / 1_000_000)
                    if stop.is_set():
                        break
        except Exception as exc:
            if not stop.is_set():
                errors.append(f'reader {index}: {type(exc).__name__}: {exc}')
        finally:
            session.close()

    def probe_requests():
        session = requests.Session()
        session.trust_env = False
        try:
            while not stop.is_set():
                started = time.perf_counter()
                response = session.get(origin + '/_benchmark/probe', timeout=5)
                response.raise_for_status()
                probe_latencies.append((time.perf_counter() - started) * 1000)
                stop.wait(.05)
        except Exception as exc:
            errors.append(f'probe: {type(exc).__name__}: {exc}')
        finally:
            session.close()

    readers = [threading.Thread(target=reader, args=(i, i >= args.clients), daemon=True)
               for i in range(len(ready))]
    probe_thread = threading.Thread(target=probe_requests, daemon=True)
    try:
        for thread in readers:
            thread.start()
        for signal in ready:
            if not signal.wait(5):
                raise RuntimeError('A stream did not connect; ' + '; '.join(errors))
        probe_thread.start()
        cpu_start, wall_start = time.process_time(), time.perf_counter()
        for number in range(args.events):
            broker.publish({'account': 'op', 'channelId': 'synthetic-channel',
                            'chatId': 'synthetic-chat', 'messageId': f'local-{number}',
                            'status': 'read', 'publishedNs': time.perf_counter_ns()})
            remaining = wall_start + (number + 1) / args.rate - time.perf_counter()
            if remaining > 0:
                stop.wait(remaining)
        deadline = time.perf_counter() + 5
        while any(len(ids) < args.events for ids in seen) and time.perf_counter() < deadline:
            stop.wait(.01)
        duration = time.perf_counter() - wall_start
        cpu_seconds = time.process_time() - cpu_start
        active_streams = broker.streams
        result = {
            'scope': 'LOCAL transport only: EventBroker.publish -> Waitress -> HTTP SSE reader',
            'excluded': ['Wazzup', 'webhook processing', 'PostgreSQL NOTIFY', 'reverse proxy',
                         'real authentication/DB reads', 'frontend refresh/debounce/render'],
            'environment': {'python': platform.python_version(), 'platform': platform.system(),
                            'waitress': importlib.metadata.version('waitress')},
            'configuration': {'clients': args.clients, 'events': args.events,
                              'events_per_second': args.rate, 'waitress_threads': args.threads,
                              'background_streams': args.background_streams},
            'deliveries': {'expected': args.clients * args.events, 'observed': sum(map(len, seen)),
                           'per_client': [len(ids) for ids in seen]},
            'publish_to_sse': summary(latencies), 'ordinary_http_get': summary(probe_latencies),
            'elapsed_seconds': round(duration, 3), 'process_cpu_seconds': round(cpu_seconds, 3),
            'process_cpu_percent_one_core': round(cpu_seconds / duration * 100, 2),
            'active_pilot_streams': active_streams, 'event_buffer_length': len(broker.events),
            'errors': errors,
        }
    finally:
        stop.set()
        broker.publish({'reload': True})
        for thread in readers:
            thread.join(2)
        if probe_thread.is_alive():
            probe_thread.join(2)
        # Disconnect is observed on the server's next write, not the read-side close.
        deadline = time.monotonic() + 2
        while broker.streams and time.monotonic() < deadline:
            broker.publish({'reload': True})
            time.sleep(.05)
        server.close()
        server.task_dispatcher.shutdown(timeout=3)
        server_thread.join(2)
    result['streams_after_disconnect'] = broker.streams
    result['passed'] = (not errors and all(len(ids) == args.events for ids in seen)
                        and broker.streams == 0 and bool(probe_latencies))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clients', type=int, default=6)
    parser.add_argument('--events', type=int, default=300)
    parser.add_argument('--rate', type=float, default=30)
    parser.add_argument('--threads', type=int, default=96)
    parser.add_argument('--background-streams', type=int, default=0)
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
